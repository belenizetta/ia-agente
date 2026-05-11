# codebase/analyzer.py
import os
import re
from typing import List, Dict, Tuple, Optional
from collections import Counter

# Optional semantic search
USE_EMBEDDINGS = os.getenv("ANALYZER_EMBEDDINGS", "0") == "1"
EMBEDDING_MODEL = os.getenv("ANALYZER_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
_MAX_FILE_CHARS = int(os.getenv("GEN_MAX_FILE_CHARS", "8000"))
_DEFAULT_LIMIT = int(os.getenv("ANALYZER_LIMIT", "8"))

# Try to import sentence-transformers if requested
_embedder = None
if USE_EMBEDDINGS:
    try:
        from sentence_transformers import SentenceTransformer, util

        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    except Exception:
        _embedder = None


class CodeAnalyzer:
    """
    Analyzer para proyectos de microservicios.
    - relevant_files(repo_dir, keywords) -> List[str] : paths relativos
    - relevant_files_with_content(repo_dir, keywords, limit) -> Dict[path, content]
    """

    IGNORE_DIRS = {".git", "__pycache__", "venv", ".venv", "env", "node_modules", ".pytest_cache"}
    # file weight boosts by name / folder hints
    PATH_WEIGHTS = {
        "router": 2.5,
        "routers": 2.5,
        "routes": 2.5,
        "controllers": 2.2,
        "views": 1.8,
        "service": 2.0,
        "services": 2.0,
        "model": 2.4,
        "models": 2.4,
        "schema": 2.2,
        "schemas": 2.2,
        "dto": 2.0,
        "db": 1.5,
        "migrations": 0.5,
        "tests": 0.9,
        "test": 0.9,
        "api": 1.6,
        "app": 1.1,
    }
    FILE_EXTS = {".py", ".js", ".ts", ".tsx", ".go", ".java", ".rb", ".json", ".yaml", ".yml"}

    def __init__(self):
        self._embedder = _embedder

    # -------------------------
    # Utilities
    # -------------------------
    def _iter_source_files(self, repo_dir: str):
        for root, dirs, files in os.walk(repo_dir):
            # prune ignored dirs
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".")]
            for fname in files:
                _, ext = os.path.splitext(fname)
                if ext.lower() in self.FILE_EXTS:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, repo_dir)
                    yield rel.replace("\\", "/"), full

    def _read_file(self, fullpath: str) -> str:
        try:
            with open(fullpath, "r", encoding="utf-8", errors="ignore") as f:
                data = f.read()
                if len(data) > _MAX_FILE_CHARS:
                    return data[:_MAX_FILE_CHARS] + "\n\n# ...TRUNCATED..."
                return data
        except Exception:
            return ""

    def _path_score_boost(self, rel: str) -> float:
        score = 1.0
        parts = re.split(r"[\\/]", rel.lower())
        for p in parts:
            for k, v in self.PATH_WEIGHTS.items():
                if k in p:
                    score *= v
        return score

    # -------------------------
    # Keyword / token helpers
    # -------------------------
    def _tokenize(self, text: str) -> List[str]:
        # simple identifier/token extraction
        return re.findall(r"[A-Za-z_]{2,}", text.lower())

    def _keyword_score(self, content: str, keywords: List[str]) -> float:
        if not keywords:
            return 0.0
        cnt = 0.0
        lower = content.lower()
        for k in keywords:
            if not k:
                continue
            # exact token frequency
            cnt += lower.count(k.lower())
        return float(cnt)

    # -------------------------
    # Embedding similarity helpers (optional)
    # -------------------------
    def _compute_embeddings(self, texts: List[str]):
        if not self._embedder:
            return None
        try:
            emb = self._embedder.encode(texts, convert_to_tensor=True, show_progress_bar=False)
            return emb
        except Exception:
            return None

    def _semantic_scores(self, files_contents: List[str], query: str) -> Optional[List[float]]:
        if not self._embedder:
            return None
        try:
            # encode query and contents
            q_emb = self._embedder.encode(query, convert_to_tensor=True)
            contents_emb = self._embedder.encode(files_contents, convert_to_tensor=True)
            sims = util.cos_sim(q_emb, contents_emb).cpu().numpy()[0]
            # return list of floats
            return [float(s) for s in sims]
        except Exception:
            return None

    # -------------------------
    # Public API: relevant_files
    # -------------------------
    def relevant_files(self, repo_dir: str, keywords: List[str], limit: int = None) -> List[str]:
        """
        Devuelve una lista de paths relativos a repo_dir ordenados por relevancia.
        - keywords: lista de tokens extraídos del prompt (pueden ser nombres, campos, intent)
        - limit: máximo número de archivos a devolver
        """
        limit = limit or _DEFAULT_LIMIT

        # 1) gather files and quick scores
        candidates: List[Tuple[str, str]] = list(self._iter_source_files(repo_dir))
        if not candidates:
            return []

        # read contents
        rels = [r for r, _ in candidates]
        fullpaths = [p for _, p in candidates]
        contents = [self._read_file(p) for p in fullpaths]

        # filename boost & keyword frequency
        base_scores: List[float] = []
        for rel, text in zip(rels, contents):
            boost = self._path_score_boost(rel)
            kscore = self._keyword_score(text, keywords)
            fname = os.path.basename(rel).lower()
            # small boost if filename contains one of keywords or entity-like tokens
            for kw in keywords:
                if kw and kw.lower() in fname:
                    kscore += 3.0
            base_scores.append(boost * (1.0 + kscore))

        # 2) optional semantic scoring if embedder available and keywords long enough
        semantic_scores = None
        query = " ".join(keywords) if keywords else ""
        if self._embedder and query.strip():
            try:
                semantic_scores = self._semantic_scores(contents, query)
            except Exception:
                semantic_scores = None

        # 3) combine scores
        combined = []
        for i, rel in enumerate(rels):
            s = base_scores[i]
            if semantic_scores:
                # scale semantic into similar range; weight configurable (0.6 sem, 0.4 base)
                s = 0.6 * (semantic_scores[i] + 1.0) + 0.4 * s
            combined.append((rel, s))

        # 4) sort and limit
        combined.sort(key=lambda x: x[1], reverse=True)

        # 5) return top-k unique list
        top = [r for r, _ in combined[:limit]]
        return top

    # -------------------------
    # Public API: files + content
    # -------------------------
    def relevant_files_with_content(self, repo_dir: str, keywords: List[str], limit: int = None) -> Dict[str, str]:
        """
        Retorna un dict: { relative_path: content } con los archivos más relevantes.
        """
        limit = limit or _DEFAULT_LIMIT
        top = self.relevant_files(repo_dir, keywords, limit=limit)
        out = {}
        for p in top:
            full = os.path.join(repo_dir, p)
            out[p] = self._read_file(full)
        return out
