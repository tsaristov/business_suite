"""Knowledge base (RAG) — document ingestion + semantic retrieval.

Adapted from mk1/rag-template/rag-cli: llama-index for chunking/embedding, a local Ollama
embedding model (nomic-embed-text by default), and a persistent ChromaDB vector store.
Unlike the CLI template this exposes *retrieval* (not a chat engine) — the assistant's
own LLM does the synthesis, so the knowledge base is just another tool it can call.

Heavy imports (llama-index, chromadb) are lazy so this module is cheap to import at tool
discovery time and a missing dependency degrades gracefully.

Layout (all git-ignored):
    data/                uploaded source documents (indexed recursively)
    .storage/chroma/     vector store
    .storage/docstore.json   ingestion docstore (enables incremental upsert/delete)
    data.json            small manifest: last sync time + chunk/file counts
"""

import importlib.util
import json
import os
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "data")
_STORAGE = os.path.join(_HERE, ".storage")
_CHROMA_DIR = os.path.join(_STORAGE, "chroma")
_DOCSTORE = os.path.join(_STORAGE, "docstore.json")
_MANIFEST = os.path.join(_HERE, "data.json")
_COLLECTION = "knowledge"
_REQUEST_TIMEOUT = 300.0

_ENGINE = {}  # cache of the lazily-built llama-index components


# --------------------------------------------------------------------------- #
# Settings (embed model, top_k) — read from assistant/settings.py by path
# --------------------------------------------------------------------------- #
def _load_settings():
    path = os.path.abspath(os.path.join(_HERE, "..", "..", "assistant", "settings.py"))
    try:
        spec = importlib.util.spec_from_file_location("assistant_settings_knowledge", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.get()
    except Exception:
        return {"embed_model": "nomic-embed-text", "top_k": 3}


# --------------------------------------------------------------------------- #
# Manifest + file listing
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now().isoformat(timespec="seconds")


def _load_manifest():
    if not os.path.exists(_MANIFEST) or os.path.getsize(_MANIFEST) == 0:
        return {"last_sync": None, "files": 0, "chunks": 0}
    try:
        with open(_MANIFEST) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"last_sync": None, "files": 0, "chunks": 0}


def _save_manifest(data):
    with open(_MANIFEST, "w") as f:
        json.dump(data, f, indent=2)


def _list_files():
    if not os.path.isdir(_DATA_DIR):
        return []
    out = []
    for root, _dirs, names in os.walk(_DATA_DIR):
        for name in names:
            if name.startswith("."):
                continue
            out.append(os.path.join(root, name))
    return sorted(out)


def _doc_entries():
    entries = []
    for path in _list_files():
        st = os.stat(path)
        entries.append({
            "name": os.path.relpath(path, _DATA_DIR),
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return entries


def data_dir():
    """Absolute path uploads should be written into (created on demand)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    return _DATA_DIR


# --------------------------------------------------------------------------- #
# llama-index engine (lazy)
# --------------------------------------------------------------------------- #
def _ensure():
    if _ENGINE.get("ready"):
        return _ENGINE
    from llama_index.core import Settings
    from llama_index.core.storage.docstore import SimpleDocumentStore
    from llama_index.embeddings.ollama import OllamaEmbedding
    from llama_index.vector_stores.chroma import ChromaVectorStore
    import chromadb

    cfg = _load_settings()
    os.makedirs(_CHROMA_DIR, exist_ok=True)
    embed = OllamaEmbedding(
        model_name=cfg.get("embed_model", "nomic-embed-text"),
        request_timeout=_REQUEST_TIMEOUT,
        embed_batch_size=32,
    )
    Settings.embed_model = embed
    client = chromadb.PersistentClient(path=_CHROMA_DIR)
    vector_store = ChromaVectorStore(
        chroma_collection=client.get_or_create_collection(_COLLECTION))
    if os.path.exists(_DOCSTORE):
        docstore = SimpleDocumentStore.from_persist_path(_DOCSTORE)
    else:
        docstore = SimpleDocumentStore()
    _ENGINE.update(embed=embed, vector_store=vector_store, docstore=docstore,
                   client=client, ready=True)
    return _ENGINE


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #
def sync():
    """(Re)index everything under data/. Incremental: new/changed files are re-embedded,
    removed files have their chunks dropped. Returns {ok, files, chunks}."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    files = _list_files()
    if not files:
        _save_manifest({"last_sync": _now(), "files": 0, "chunks": 0})
        return {"ok": True, "files": 0, "chunks": 0}
    try:
        engine = _ensure()
        from llama_index.core import SimpleDirectoryReader
        from llama_index.core.ingestion import IngestionPipeline, DocstoreStrategy
        from llama_index.core.node_parser import SentenceSplitter

        reader = SimpleDirectoryReader(_DATA_DIR, recursive=True, filename_as_id=True)
        docs = reader.load_data()
        pipeline = IngestionPipeline(
            transformations=[SentenceSplitter(), engine["embed"]],
            docstore=engine["docstore"],
            vector_store=engine["vector_store"],
            docstore_strategy=DocstoreStrategy.UPSERTS_AND_DELETE,
        )
        nodes = pipeline.run(documents=docs)
        os.makedirs(_STORAGE, exist_ok=True)
        engine["docstore"].persist(_DOCSTORE)
    except Exception as e:
        return {"ok": False, "error": f"indexing failed: {e}"}
    manifest = {"last_sync": _now(), "files": len(files), "chunks": len(nodes)}
    _save_manifest(manifest)
    return {"ok": True, **manifest}


def search(query, top_k=None):
    """Semantic search over the indexed documents. Returns {ok, results:[{text,source,score}]}."""
    if not str(query or "").strip():
        return {"ok": False, "error": "query is required"}
    if not _list_files():
        return {"ok": True, "results": [], "count": 0, "note": "knowledge base is empty"}
    try:
        engine = _ensure()
        from llama_index.core import VectorStoreIndex

        cfg = _load_settings()
        k = int(top_k or cfg.get("top_k", 3))
        index = VectorStoreIndex.from_vector_store(
            engine["vector_store"], embed_model=engine["embed"])
        nodes = index.as_retriever(similarity_top_k=k).retrieve(str(query))
    except Exception as e:
        return {"ok": False, "error": f"search failed: {e}"}
    results = [{
        "text": n.get_content(),
        "source": n.metadata.get("file_name", n.metadata.get("filename", "?")),
        "score": round(float(n.score or 0.0), 4),
    } for n in nodes]
    return {"ok": True, "results": results, "count": len(results)}


def list_documents():
    """Indexed source documents plus last-sync info."""
    return {"ok": True, "documents": _doc_entries(), "index": _load_manifest()}


def remove_document(name):
    """Delete a source document and re-index so its chunks are dropped."""
    path = os.path.join(_DATA_DIR, name)
    if not os.path.abspath(path).startswith(os.path.abspath(_DATA_DIR)):
        return {"ok": False, "error": "invalid document name"}
    if not os.path.isfile(path):
        return {"ok": False, "error": "no such document"}
    os.remove(path)
    result = sync()
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "reindex failed")}
    return {"ok": True, "removed": name, "chunks": result.get("chunks", 0)}
