#!/usr/bin/env python3
"""Unsupervised ML pipeline for IFU interface signal classification.

Uses graph-regularized deep embedding + HDBSCAN to discover natural clusters
in the interface signal space, then labels each cluster using interpretable
signal-name and connectivity features.

Pipeline stages:
  1. Feature extraction: multi-hot connectivity, signal-name tokens, structural
  2. Denoising autoencoder: learns compressed embedding with graph-consistency loss
  3. UMAP projection: for visualization and HDBSCAN input
  4. HDBSCAN clustering: density-based cluster discovery
  5. Cluster labeling: name each cluster using dominant signal features
  6. Output: scored CSV + cluster summary + 2D scatter plot

Dependencies: numpy, pandas, scikit-learn, hdbscan, umap-learn, scipy
Optional: matplotlib (for scatter plot)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.preprocessing import StandardScaler, LabelEncoder

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")
PIPELINE_STAGE_RE = re.compile(r"M(\d{3})H")
WIDTH_NUMERIC_RE = re.compile(r"\[(\d+):(\d+)\]")

# Well-known functional token groups (for cluster labeling)
FUNCTIONAL_TOKEN_MAP = {
    "clock_reset_power": {"clk", "clock", "reset", "rst", "pwr", "power", "pm", "pmclck"},
    "snoop_coherency": {"snp", "snoop", "coh", "mli", "snpid", "snpval", "snphit"},
    "request_command": {"req", "request", "cmd", "dispatch", "issue", "alloc"},
    "response_completion": {"rsp", "resp", "cmpl", "ack", "nack", "done", "cmp"},
    "data_payload": {"data", "payload", "addr", "tag", "uri", "phyadr", "adr"},
    "control_flow": {"stall", "flow", "credit", "valid", "ready", "enable", "en", "rdy"},
    "status_error": {"status", "error", "err", "fault", "poison", "warn"},
    "debug_dft_test": {"fscan", "jtag", "ijtag", "misr", "lbist", "mbist", "bist",
                       "scan", "dfx", "tap", "tdr", "debug", "trace"},
    "branch_prediction": {"bp", "bpc", "bpb", "bpfetch", "rsmo", "rsmoclear",
                          "bac", "fetch", "predict"},
    "tlb_memory": {"tlb", "mem", "mcache", "icache", "cache", "array", "iftlb",
                   "ifmem", "ifdata", "latch", "sb", "swpref"},
    "queue_buffer": {"ifq", "queue", "buf", "entry", "ptr", "fifo", "alloc", "dealloc"},
    "interrupt_exception": {"intr", "interrupt", "nmi", "smi", "cmc", "corrmc",
                            "exception", "fault", "mc"},
    "thread_control": {"thread", "smt", "thstate", "curth", "logical", "coreid"},
    "control_register": {"crbit", "crbrt", "cr", "ifcr", "chicken", "ctl", "cfg",
                         "config", "msr"},
}

NOISY_BLOCKS = {
    "FE_RTLSI_MON", "BPU_TLM", "FE_EVENTS_TLM", "IFU_ILG_BAC_TLM",
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def tokenize_signal_name(name: str) -> list[str]:
    """Split a signal name into semantic tokens."""
    parts = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name or ""):
        if not chunk:
            continue
        parts.extend(CAMEL_RE.findall(chunk))
    return [p.lower() for p in parts if p]


def split_units(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw or raw == "NONE":
        return []
    return [u.strip() for u in raw.split(";") if u.strip() and u.strip() != "NONE"]


def unit_leaf(unit: str) -> str:
    tokens = [t.strip() for t in re.split(r"<-|->", unit) if t.strip()]
    return tokens[-1] if tokens else unit.strip()


def is_noisy_block(name: str) -> bool:
    v = (name or "").strip().upper()
    if not v:
        return False
    return v in NOISY_BLOCKS or v.endswith("_TLM") or v.endswith("_MON")


def extract_pipeline_stage(name: str) -> int:
    """Extract pipeline stage number from signal name, or -1."""
    m = PIPELINE_STAGE_RE.search(name or "")
    return int(m.group(1)) if m else -1


def parse_width_bits(width_str: str) -> int:
    """Parse packed width expression to estimated bit count."""
    if not width_str or not width_str.strip():
        return 1
    m = WIDTH_NUMERIC_RE.search(width_str)
    if m:
        return abs(int(m.group(1)) - int(m.group(2))) + 1
    # Parametric width - count commas as heuristic for multi-dim
    return 0  # unknown parametric


# ──────────────────────────────────────────────────────────────────────────────
# Feature Builder
# ──────────────────────────────────────────────────────────────────────────────

class FeatureBuilder:
    """Build a numerical feature matrix from the IO table CSV rows."""

    def __init__(self, rows: list[dict[str, str]],
                 nl_descriptions: dict[str, str] | None = None):
        self.rows = rows
        self.n = len(rows)
        self.nl_descriptions = nl_descriptions or {}

        # Vocabulary building pass
        self._build_vocabularies()

    def _build_vocabularies(self):
        """Build vocabs for multi-hot encoding."""
        # Signal tokens
        sig_token_counter: Counter = Counter()
        src_unit_counter: Counter = Counter()
        peer_unit_counter: Counter = Counter()
        tlm_unit_counter: Counter = Counter()
        sv_type_counter: Counter = Counter()
        owner_counter: Counter = Counter()

        for r in self.rows:
            sig_token_counter.update(tokenize_signal_name(r.get("port_name", "")))
            for u in split_units(r.get("source_output_units", "")):
                leaf = unit_leaf(u).upper()
                if not is_noisy_block(leaf):
                    src_unit_counter[leaf] += 1
            for u in split_units(r.get("connected_other_units", "")):
                leaf = unit_leaf(u).upper()
                if not is_noisy_block(leaf):
                    peer_unit_counter[leaf] += 1
            for u in split_units(r.get("connected_tlm_units", "")):
                tlm_unit_counter[unit_leaf(u).upper()] += 1
            sv = (r.get("direction_sv_type") or "").strip()
            if sv:
                sv_type_counter[sv] += 1
            ow = (r.get("producer_cluster_owner") or "").strip().upper()
            if ow and ow != "NONE":
                owner_counter[ow] += 1

        # Keep tokens appearing >= 2 times
        self.sig_vocab = sorted(t for t, c in sig_token_counter.items() if c >= 2)
        self.src_vocab = sorted(t for t, c in src_unit_counter.items() if c >= 2)
        self.peer_vocab = sorted(t for t, c in peer_unit_counter.items() if c >= 2)
        self.tlm_vocab = sorted(t for t, c in tlm_unit_counter.items() if c >= 2)
        self.sv_type_vocab = sorted(t for t, c in sv_type_counter.items() if c >= 2)
        self.owner_vocab = sorted(t for t, c in owner_counter.items() if c >= 2)

        # Functional token groups
        self.func_groups = list(FUNCTIONAL_TOKEN_MAP.keys())

        # Feature names for interpretability
        self.feature_names: list[str] = []

    def build(self) -> np.ndarray:
        """Build the full feature matrix [n_signals x n_features]."""
        features_list = []

        # 1. Signal-name token multi-hot (TF-IDF weighted)
        sig_idx = {t: i for i, t in enumerate(self.sig_vocab)}
        sig_mat = np.zeros((self.n, len(self.sig_vocab)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            tokens = tokenize_signal_name(r.get("port_name", ""))
            token_counts = Counter(tokens)
            for t, cnt in token_counts.items():
                if t in sig_idx:
                    sig_mat[i, sig_idx[t]] = cnt
        # Apply TF-IDF weighting
        doc_freq = np.sum(sig_mat > 0, axis=0) + 1
        idf = np.log(self.n / doc_freq)
        sig_mat *= idf
        self.feature_names.extend(f"sig:{t}" for t in self.sig_vocab)
        features_list.append(sig_mat)

        # 2. Source unit multi-hot
        src_idx = {t: i for i, t in enumerate(self.src_vocab)}
        src_mat = np.zeros((self.n, len(self.src_vocab)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            for u in split_units(r.get("source_output_units", "")):
                leaf = unit_leaf(u).upper()
                if leaf in src_idx:
                    src_mat[i, src_idx[leaf]] = 1.0
        self.feature_names.extend(f"src:{t}" for t in self.src_vocab)
        features_list.append(src_mat)

        # 3. Peer unit multi-hot
        peer_idx = {t: i for i, t in enumerate(self.peer_vocab)}
        peer_mat = np.zeros((self.n, len(self.peer_vocab)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            for u in split_units(r.get("connected_other_units", "")):
                leaf = unit_leaf(u).upper()
                if leaf in peer_idx:
                    peer_mat[i, peer_idx[leaf]] = 1.0
        self.feature_names.extend(f"peer:{t}" for t in self.peer_vocab)
        features_list.append(peer_mat)

        # 4. TLM unit multi-hot (downweighted by 0.3)
        tlm_idx = {t: i for i, t in enumerate(self.tlm_vocab)}
        tlm_mat = np.zeros((self.n, len(self.tlm_vocab)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            for u in split_units(r.get("connected_tlm_units", "")):
                leaf = unit_leaf(u).upper()
                if leaf in tlm_idx:
                    tlm_mat[i, tlm_idx[leaf]] = 0.3
        self.feature_names.extend(f"tlm:{t}" for t in self.tlm_vocab)
        features_list.append(tlm_mat)

        # 5. Functional token group scores
        func_mat = np.zeros((self.n, len(self.func_groups)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            tokens_set = set(tokenize_signal_name(r.get("port_name", "")))
            for j, group in enumerate(self.func_groups):
                hits = len(tokens_set & FUNCTIONAL_TOKEN_MAP[group])
                func_mat[i, j] = hits
        self.feature_names.extend(f"func:{g}" for g in self.func_groups)
        features_list.append(func_mat)

        # 6. Scalar features
        scalar_names = []
        scalar_list = []

        # Direction (input=0, output=1)
        dirs = np.array([1.0 if r.get("port_direction", "").strip().lower() == "output" else 0.0
                         for r in self.rows], dtype=np.float32).reshape(-1, 1)
        scalar_names.append("is_output")
        scalar_list.append(dirs)

        # Pipeline stage (normalized)
        stages = np.array([extract_pipeline_stage(r.get("port_name", ""))
                           for r in self.rows], dtype=np.float32)
        has_stage = stages >= 0
        stages_norm = np.where(has_stage, stages / 999.0, -0.1).reshape(-1, 1)
        scalar_names.append("pipeline_stage_norm")
        scalar_list.append(stages_norm)

        # Has pipeline stage
        scalar_names.append("has_pipeline_stage")
        scalar_list.append(has_stage.astype(np.float32).reshape(-1, 1))

        # Width (estimated bits, log-scaled)
        widths = np.array([parse_width_bits(r.get("direction_packed_width", ""))
                           for r in self.rows], dtype=np.float32)
        widths_log = np.log1p(widths).reshape(-1, 1)
        scalar_names.append("width_log")
        scalar_list.append(widths_log)

        # Is parametric width (not a numeric constant)
        is_param = np.array([1.0 if widths[i] == 0 and (self.rows[i].get("direction_packed_width") or "").strip()
                             else 0.0 for i in range(self.n)], dtype=np.float32).reshape(-1, 1)
        scalar_names.append("is_parametric_width")
        scalar_list.append(is_param)

        # Has struct type
        is_struct = np.array([1.0 if (r.get("direction_sv_type") or "").startswith("t_") else 0.0
                              for r in self.rows], dtype=np.float32).reshape(-1, 1)
        scalar_names.append("is_struct_type")
        scalar_list.append(is_struct)

        # Number of source units
        n_src = np.array([len(split_units(r.get("source_output_units", "")))
                          for r in self.rows], dtype=np.float32).reshape(-1, 1)
        scalar_names.append("n_source_units")
        scalar_list.append(n_src)

        # Number of peer units
        n_peer = np.array([len(split_units(r.get("connected_other_units", "")))
                           for r in self.rows], dtype=np.float32).reshape(-1, 1)
        scalar_names.append("n_peer_units")
        scalar_list.append(n_peer)

        # Owner encoding
        owner_idx = {t: i for i, t in enumerate(self.owner_vocab)}
        owner_mat = np.zeros((self.n, len(self.owner_vocab)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            ow = (r.get("producer_cluster_owner") or "").strip().upper()
            if ow in owner_idx:
                owner_mat[i, owner_idx[ow]] = 1.0
        self.feature_names.extend(f"owner:{t}" for t in self.owner_vocab)

        # SV type encoding
        svt_idx = {t: i for i, t in enumerate(self.sv_type_vocab)}
        svt_mat = np.zeros((self.n, len(self.sv_type_vocab)), dtype=np.float32)
        for i, r in enumerate(self.rows):
            sv = (r.get("direction_sv_type") or "").strip()
            if sv in svt_idx:
                svt_mat[i, svt_idx[sv]] = 1.0
        self.feature_names.extend(f"svtype:{t}" for t in self.sv_type_vocab)

        scalars = np.hstack(scalar_list)
        self.feature_names.extend(scalar_names)

        extra_mats = [scalars, owner_mat, svt_mat]

        # NL description TF-IDF features (if provided)
        if self.nl_descriptions:
            nl_mat = self._build_nl_tfidf()
            if nl_mat is not None:
                extra_mats.append(nl_mat)

        # Combine all
        X = np.hstack(features_list + extra_mats)
        return X

    def _build_nl_tfidf(self) -> np.ndarray | None:
        """Build TF-IDF feature matrix from NL descriptions."""
        # Tokenize descriptions
        desc_token_counter: Counter = Counter()
        desc_tokens_per_row: list[list[str]] = []
        stop_words = {"the", "a", "an", "is", "in", "to", "of", "for", "and",
                      "from", "at", "by", "or", "its", "it", "this", "that",
                      "with", "as", "on", "be", "are", "was", "ifu", "signal",
                      "input", "output", "into", "across", "active", "stage",
                      "width", "replicated", "structured", "provides", "carries",
                      "consumed", "produced", "received", "delivered", "owned",
                      "sourced", "cluster", "context", "rtl", "pipeline"}
        for r in self.rows:
            name = r.get("port_name", "")
            desc = self.nl_descriptions.get(name, "")
            # Simple word tokenization (lowercase, alpha-only)
            words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", desc)]
            words = [w for w in words if w not in stop_words]
            desc_tokens_per_row.append(words)
            desc_token_counter.update(set(words))  # use set for doc-freq

        # Keep tokens appearing in >= 3 and <= 80% of documents
        max_df = int(self.n * 0.8)
        nl_vocab = sorted(t for t, c in desc_token_counter.items()
                          if 3 <= c <= max_df)
        if not nl_vocab:
            return None

        nl_idx = {t: i for i, t in enumerate(nl_vocab)}
        nl_mat = np.zeros((self.n, len(nl_vocab)), dtype=np.float32)

        for i, words in enumerate(desc_tokens_per_row):
            word_counts = Counter(words)
            for w, cnt in word_counts.items():
                if w in nl_idx:
                    nl_mat[i, nl_idx[w]] = cnt

        # TF-IDF weighting
        doc_freq = np.sum(nl_mat > 0, axis=0) + 1
        idf = np.log(self.n / doc_freq)
        nl_mat *= idf

        self.feature_names.extend(f"nl:{t}" for t in nl_vocab)
        print(f"  NL description features: {len(nl_vocab)} tokens")
        return nl_mat

    def build_adjacency(self) -> np.ndarray:
        """Build a graph adjacency matrix based on shared connectivity.

        Two signals are connected if they share source_output_units or
        connected_other_units (non-noisy) connections.
        """
        # Build unit->signal_indices mapping
        unit_to_signals: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(self.rows):
            for field in ["source_output_units", "connected_other_units"]:
                for u in split_units(r.get(field, "")):
                    leaf = unit_leaf(u).upper()
                    if not is_noisy_block(leaf):
                        unit_to_signals[leaf].append(i)

        # Build adjacency: signals sharing a unit are neighbors
        adj = np.zeros((self.n, self.n), dtype=np.float32)
        for unit, indices in unit_to_signals.items():
            for a in indices:
                for b in indices:
                    if a != b:
                        adj[a, b] = 1.0

        # Normalize rows to make it a transition/averaging matrix
        row_sums = adj.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums > 0, row_sums, 1.0)
        adj_norm = adj / row_sums
        return adj_norm


def _precompute_sparse_adj(adj: np.ndarray, k: int = 10):
    """Build a sparse row-normalized top-k adjacency + connectivity mask.

    Returns (adj_sparse: csr_matrix, has_neighbors: ndarray[n,1]).
    """
    n = adj.shape[0]
    rows_l, cols_l, vals_l = [], [], []
    conn = np.zeros(n, dtype=np.float32)
    for i in range(n):
        nz = np.nonzero(adj[i])[0]
        if len(nz) == 0:
            continue
        conn[i] = 1.0
        sel = nz if len(nz) <= k else nz[np.argpartition(adj[i, nz], -k)[-k:]]
        w = adj[i, sel].astype(np.float32)
        w /= w.sum()
        for j_idx, j in enumerate(sel):
            rows_l.append(i)
            cols_l.append(j)
            vals_l.append(w[j_idx])
    sp = csr_matrix(
        (np.array(vals_l, dtype=np.float32),
         (np.array(rows_l, dtype=np.int64), np.array(cols_l, dtype=np.int64))),
        shape=(n, n),
    )
    return sp, conn.reshape(-1, 1)


# ──────────────────────────────────────────────────────────────────────────────
# Denoising Autoencoder (pure numpy)
# ──────────────────────────────────────────────────────────────────────────────

class DenoisingAutoencoder:
    """Simple denoising autoencoder implemented in numpy.

    Architecture: input -> enc1 -> enc2 (bottleneck) -> dec1 -> output
    Training: masked input reconstruction + graph-consistency loss
    """

    def __init__(self, input_dim: int, hidden1: int = 128, bottleneck: int = 32,
                 noise_frac: float = 0.3, lr: float = 0.005, graph_weight: float = 0.5):
        self.noise_frac = noise_frac
        self.lr = lr
        self.graph_weight = graph_weight

        # Xavier initialization
        self.W1 = np.random.randn(input_dim, hidden1).astype(np.float32) * np.sqrt(2.0 / (input_dim + hidden1))
        self.b1 = np.zeros(hidden1, dtype=np.float32)
        self.W2 = np.random.randn(hidden1, bottleneck).astype(np.float32) * np.sqrt(2.0 / (hidden1 + bottleneck))
        self.b2 = np.zeros(bottleneck, dtype=np.float32)
        self.W3 = np.random.randn(bottleneck, hidden1).astype(np.float32) * np.sqrt(2.0 / (bottleneck + hidden1))
        self.b3 = np.zeros(hidden1, dtype=np.float32)
        self.W4 = np.random.randn(hidden1, input_dim).astype(np.float32) * np.sqrt(2.0 / (hidden1 + input_dim))
        self.b4 = np.zeros(input_dim, dtype=np.float32)

    @staticmethod
    def _relu(x):
        return np.maximum(0, x)

    @staticmethod
    def _relu_grad(x):
        return (x > 0).astype(np.float32)

    def _forward(self, X):
        """Forward pass, returns intermediate values for backprop."""
        z1 = X @ self.W1 + self.b1
        a1 = self._relu(z1)
        z2 = a1 @ self.W2 + self.b2
        a2 = self._relu(z2)  # bottleneck
        z3 = a2 @ self.W3 + self.b3
        a3 = self._relu(z3)
        z4 = a3 @ self.W4 + self.b4
        return z1, a1, z2, a2, z3, a3, z4

    def encode(self, X: np.ndarray) -> np.ndarray:
        """Get bottleneck embedding."""
        a1 = self._relu(X @ self.W1 + self.b1)
        a2 = self._relu(a1 @ self.W2 + self.b2)
        return a2

    def _precompute_sparse_adj(self, adj: np.ndarray, k: int = 10):
        """Kept for backward compat - prefer module-level _precompute_sparse_adj."""
        sp, hn = _precompute_sparse_adj(adj, k)
        self._adj_sparse = sp
        self._has_neighbors = hn

    def train(self, X: np.ndarray, adj_sparse, has_neighbors: np.ndarray,
              epochs: int = 150, verbose: bool = True):
        """Train with reconstruction + graph-consistency loss.

        adj_sparse: csr_matrix row-normalized sparse adjacency [n,n].
        has_neighbors: [n,1] float mask (1.0 = has neighbors).
        All ops are vectorized matrix multiplications.
        """
        n = X.shape[0]
        n_connected = float(has_neighbors.sum())
        rng = np.random.default_rng(42)

        for epoch in range(epochs):
            # Add masking noise
            mask = rng.random(X.shape) > self.noise_frac
            X_noisy = X * mask

            # Full-batch forward
            z1, a1, z2, a2, z3, a3, z4 = self._forward(X_noisy)
            X_hat = z4

            # Reconstruction loss (MSE)
            diff = X_hat - X
            recon_loss = np.mean(diff ** 2)

            # Graph consistency loss: || neighbor_avg(embed) - embed ||^2
            embed = a2  # [n, bottleneck]
            neighbor_avg = adj_sparse.dot(embed)  # sparse @ dense -> [n, bottleneck]
            graph_delta = (embed - neighbor_avg) * has_neighbors
            graph_loss = np.sum(graph_delta ** 2) / max(n_connected, 1.0)

            # Backprop: reconstruction gradient
            scale = 2.0 / (n * X.shape[1])
            d4 = diff * scale
            dW4 = a3.T @ d4
            db4 = d4.sum(axis=0)
            d3 = (d4 @ self.W4.T) * self._relu_grad(z3)
            dW3 = a2.T @ d3
            db3 = d3.sum(axis=0)
            d2 = (d3 @ self.W3.T) * self._relu_grad(z2)

            # Graph gradient
            if self.graph_weight > 0 and n_connected > 0:
                d_graph = (2.0 * graph_delta / n_connected) * self.graph_weight
                d2 += d_graph * self._relu_grad(z2)

            dW2 = a1.T @ d2
            db2 = d2.sum(axis=0)
            d1 = (d2 @ self.W2.T) * self._relu_grad(z1)
            dW1 = X_noisy.T @ d1
            db1 = d1.sum(axis=0)

            # Gradient clipping
            max_norm = 5.0
            for g in [dW1, db1, dW2, db2, dW3, db3, dW4, db4]:
                gnorm = np.linalg.norm(g)
                if gnorm > max_norm:
                    g *= max_norm / gnorm

            # Update weights
            self.W1 -= self.lr * dW1
            self.b1 -= self.lr * db1
            self.W2 -= self.lr * dW2
            self.b2 -= self.lr * db2
            self.W3 -= self.lr * dW3
            self.b3 -= self.lr * db3
            self.W4 -= self.lr * dW4
            self.b4 -= self.lr * db4

            if verbose and (epoch % 50 == 0 or epoch == epochs - 1):
                print(f"  epoch {epoch:3d}: recon={recon_loss:.5f}  graph={graph_loss:.5f}")


# ──────────────────────────────────────────────────────────────────────────────
# Cluster Labeler
# ──────────────────────────────────────────────────────────────────────────────

def label_cluster(cluster_rows: list[dict[str, str]], cluster_id: int) -> dict[str, Any]:
    """Assign an interpretable label to a cluster based on its members."""
    # Collect tokens from all signal names in the cluster
    all_tokens: Counter = Counter()
    src_units: Counter = Counter()
    peer_units: Counter = Counter()
    directions: Counter = Counter()
    stages: list[int] = []
    owners: Counter = Counter()

    for r in cluster_rows:
        all_tokens.update(tokenize_signal_name(r.get("port_name", "")))
        for u in split_units(r.get("source_output_units", "")):
            leaf = unit_leaf(u).upper()
            if not is_noisy_block(leaf):
                src_units[leaf] += 1
        for u in split_units(r.get("connected_other_units", "")):
            leaf = unit_leaf(u).upper()
            if not is_noisy_block(leaf):
                peer_units[leaf] += 1
        directions[r.get("port_direction", "unknown")] += 1
        st = extract_pipeline_stage(r.get("port_name", ""))
        if st >= 0:
            stages.append(st)
        ow = (r.get("producer_cluster_owner") or "").strip().upper()
        if ow and ow != "NONE":
            owners[ow] += 1

    # Score each functional group
    group_scores: dict[str, float] = {}
    for group, keywords in FUNCTIONAL_TOKEN_MAP.items():
        score = sum(all_tokens.get(kw, 0) for kw in keywords)
        group_scores[group] = score

    best_group = max(group_scores, key=group_scores.get)
    best_score = group_scores[best_group]

    # Build label string
    dominant_unit = src_units.most_common(1)[0][0] if src_units else (
        peer_units.most_common(1)[0][0] if peer_units else "MIXED")
    dominant_dir = directions.most_common(1)[0][0]

    if best_score > 0:
        label = f"{best_group}:{dominant_unit}"
    else:
        label = f"misc:{dominant_unit}"

    stage_range = ""
    if stages:
        stage_range = f"M{min(stages):03d}H-M{max(stages):03d}H"

    return {
        "cluster_id": cluster_id,
        "label": label,
        "size": len(cluster_rows),
        "functional_group": best_group if best_score > 0 else "misc_unknown",
        "functional_score": best_score,
        "dominant_unit": dominant_unit,
        "dominant_direction": dominant_dir,
        "stage_range": stage_range,
        "top_tokens": [t for t, _ in all_tokens.most_common(10)],
        "top_src_units": [u for u, _ in src_units.most_common(5)],
        "top_peer_units": [u for u, _ in peer_units.most_common(5)],
        "top_owners": [o for o, _ in owners.most_common(5)],
        "group_scores": {k: v for k, v in sorted(group_scores.items(), key=lambda x: -x[1]) if v > 0},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main Pipeline
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--io-csv", required=True,
                        help="Input IO table CSV (e.g. 00_ifu_top_interface_from_drilldown.csv)")
    parser.add_argument("--out-dir", required=True,
                        help="Output directory for results")
    parser.add_argument("--module", default="ifu",
                        help="Module name (used in output file naming)")

    # Autoencoder params
    parser.add_argument("--ae-hidden", type=int, default=64,
                        help="Autoencoder first hidden layer size")
    parser.add_argument("--ae-bottleneck", type=int, default=24,
                        help="Autoencoder bottleneck (embedding) size")
    parser.add_argument("--ae-epochs", type=int, default=150,
                        help="Autoencoder training epochs")
    parser.add_argument("--ae-lr", type=float, default=0.003,
                        help="Autoencoder learning rate")
    parser.add_argument("--ae-noise", type=float, default=0.2,
                        help="Fraction of input features to mask during training")
    parser.add_argument("--graph-weight", type=float, default=0.3,
                        help="Weight for graph-consistency loss term")

    # HDBSCAN params
    parser.add_argument("--hdbscan-min-cluster", type=int, default=8,
                        help="HDBSCAN min_cluster_size")
    parser.add_argument("--hdbscan-min-samples", type=int, default=5,
                        help="HDBSCAN min_samples")

    # UMAP params
    parser.add_argument("--umap-neighbors", type=int, default=15,
                        help="UMAP n_neighbors")
    parser.add_argument("--umap-min-dist", type=float, default=0.1,
                        help="UMAP min_dist")
    parser.add_argument("--umap-components", type=int, default=2,
                        help="UMAP output dimensions")

    # NL description features
    parser.add_argument("--nl-descriptions-csv", default="",
                        help="CSV with nl_description column (from generate_signal_descriptions.py)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Create output directory
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load CSV
    print(f"[1/6] Loading {args.io_csv} ...")
    with open(args.io_csv) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    print(f"  Loaded {len(rows)} rows, {len(fieldnames)} columns")

    # Filter out signals ending in _inst (struct instance wrappers, not real ports)
    rows_excluded = [r for r in rows if r.get("port_name", "").endswith("_inst")]
    rows = [r for r in rows if not r.get("port_name", "").endswith("_inst")]
    print(f"  Excluded {len(rows_excluded)} _inst signals, {len(rows)} remaining")

    if len(rows) < 20:
        print("ERROR: Too few rows for clustering. Need at least 20.")
        sys.exit(1)

    # Load NL descriptions if provided
    nl_descriptions: dict[str, str] = {}
    if args.nl_descriptions_csv:
        print(f"  Loading NL descriptions from {args.nl_descriptions_csv} ...")
        with open(args.nl_descriptions_csv) as f:
            for r in csv.DictReader(f):
                name = r.get("port_name", "")
                desc = r.get("nl_description", "")
                if name and desc and desc != "EXCLUDED (_inst struct wrapper)":
                    nl_descriptions[name] = desc
        print(f"  Loaded {len(nl_descriptions)} NL descriptions")

    # ── Stage 1: Feature extraction ──
    print(f"[2/6] Building features ...")
    fb = FeatureBuilder(rows, nl_descriptions=nl_descriptions)
    X_raw = fb.build()
    adj = fb.build_adjacency()
    print(f"  Feature matrix: {X_raw.shape} ({len(fb.feature_names)} named features)")
    print(f"  Adjacency: {adj.shape}, {np.sum(adj > 0)} non-zero entries")

    # Standardize
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_raw)

    # ── Stage 2: Denoising autoencoder with graph loss ──
    print(f"[3/6] Training denoising autoencoder (epochs={args.ae_epochs}, "
          f"bottleneck={args.ae_bottleneck}, graph_weight={args.graph_weight}) ...")
    ae = DenoisingAutoencoder(
        input_dim=X_scaled.shape[1],
        hidden1=args.ae_hidden,
        bottleneck=args.ae_bottleneck,
        noise_frac=args.ae_noise,
        lr=args.ae_lr,
        graph_weight=args.graph_weight,
    )
    # Precompute sparse adjacency for efficient training
    from scipy.sparse import csr_matrix as _csr
    adj_sparse, has_neighbors = _precompute_sparse_adj(adj, k=10)
    ae.train(X_scaled, adj_sparse, has_neighbors, epochs=args.ae_epochs, verbose=True)
    embeddings = ae.encode(X_scaled)
    print(f"  Embedding shape: {embeddings.shape}")

    # ── Stage 3: UMAP projection ──
    print(f"[4/6] UMAP projection (neighbors={args.umap_neighbors}, "
          f"min_dist={args.umap_min_dist}) ...")
    import umap
    reducer = umap.UMAP(
        n_neighbors=args.umap_neighbors,
        min_dist=args.umap_min_dist,
        n_components=args.umap_components,
        metric="euclidean",
        random_state=42,
    )
    X_2d = reducer.fit_transform(embeddings)
    print(f"  UMAP output: {X_2d.shape}")

    # ── Stage 4: HDBSCAN clustering ──
    print(f"[5/6] HDBSCAN clustering (min_cluster={args.hdbscan_min_cluster}, "
          f"min_samples={args.hdbscan_min_samples}) ...")
    # Prefer sklearn's HDBSCAN (1.3+) for API compatibility; fall back to hdbscan package
    try:
        from sklearn.cluster import HDBSCAN as SkHDBSCAN
        clusterer = SkHDBSCAN(
            min_cluster_size=args.hdbscan_min_cluster,
            min_samples=args.hdbscan_min_samples,
            cluster_selection_method="eom",
        )
        cluster_labels = clusterer.fit_predict(X_2d)
        probabilities = clusterer.probabilities_
    except (ImportError, AttributeError):
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=args.hdbscan_min_cluster,
            min_samples=args.hdbscan_min_samples,
            cluster_selection_method="eom",
        )
        cluster_labels = clusterer.fit_predict(X_2d)
        probabilities = clusterer.probabilities_

    n_clusters = len(set(cluster_labels)) - (1 if -1 in cluster_labels else 0)
    n_noise = np.sum(cluster_labels == -1)
    print(f"  Found {n_clusters} clusters, {n_noise} noise points")

    # ── Stage 5: Cluster labeling ──
    print(f"[6/6] Labeling clusters and writing outputs ...")
    cluster_to_rows: dict[int, list[dict[str, str]]] = defaultdict(list)
    for i, r in enumerate(rows):
        cluster_to_rows[cluster_labels[i]].append(r)

    cluster_info: dict[int, dict[str, Any]] = {}
    for cid in sorted(set(cluster_labels)):
        if cid == -1:
            cluster_info[cid] = {
                "cluster_id": -1,
                "label": "noise:unassigned",
                "size": len(cluster_to_rows[cid]),
                "functional_group": "noise",
                "dominant_unit": "MIXED",
            }
        else:
            cluster_info[cid] = label_cluster(cluster_to_rows[cid], cid)

    # ── Write outputs ──
    # 1. Scored CSV — insert ml_functional_group right before port_name
    scored_csv_path = out_dir / f"{args.module}_ml_classified.csv"
    extra_cols = ["ml_cluster_id", "ml_cluster_label",
                  "ml_confidence", "ml_umap_x", "ml_umap_y", "ml_review_flag"]
    # Build column order: insert ml_functional_group before port_name
    out_fieldnames = []
    for col in fieldnames:
        if col == "port_name":
            out_fieldnames.append("ml_functional_group")
        out_fieldnames.append(col)
    if "ml_functional_group" not in out_fieldnames:
        out_fieldnames.insert(0, "ml_functional_group")
    out_fieldnames.extend(extra_cols)
    with open(scored_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        for i, r in enumerate(rows):
            cid = int(cluster_labels[i])
            info = cluster_info[cid]
            out_row = dict(r)
            out_row["ml_cluster_id"] = cid
            out_row["ml_cluster_label"] = info.get("label", "unknown")
            out_row["ml_functional_group"] = info.get("functional_group", "unknown")
            out_row["ml_confidence"] = f"{probabilities[i]:.3f}"
            out_row["ml_umap_x"] = f"{X_2d[i, 0]:.4f}"
            out_row["ml_umap_y"] = f"{X_2d[i, 1]:.4f}"
            # Flag low-confidence or noise points for review
            out_row["ml_review_flag"] = "REVIEW" if (cid == -1 or probabilities[i] < 0.5) else ""
            writer.writerow(out_row)
        # Append excluded _inst signals with "EXCLUDED" markers
        for r in rows_excluded:
            out_row = dict(r)
            out_row["ml_cluster_id"] = -2
            out_row["ml_cluster_label"] = "excluded:inst_wrapper"
            out_row["ml_functional_group"] = "excluded"
            out_row["ml_confidence"] = ""
            out_row["ml_umap_x"] = ""
            out_row["ml_umap_y"] = ""
            out_row["ml_review_flag"] = "EXCLUDED"
            writer.writerow(out_row)
    print(f"  Wrote {scored_csv_path} ({len(rows)} classified + {len(rows_excluded)} excluded)")

    # 2. Cluster summary CSV
    summary_csv_path = out_dir / f"{args.module}_ml_cluster_summary.csv"
    summary_fields = ["cluster_id", "label", "size", "functional_group", "functional_score",
                      "dominant_unit", "dominant_direction", "stage_range",
                      "top_tokens", "top_src_units", "top_peer_units", "top_owners"]
    with open(summary_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        for cid in sorted(cluster_info.keys()):
            info = cluster_info[cid]
            row = {k: info.get(k, "") for k in summary_fields}
            # Convert lists to strings
            for k in ["top_tokens", "top_src_units", "top_peer_units", "top_owners"]:
                if isinstance(row.get(k), list):
                    row[k] = ";".join(row[k])
            writer.writerow(row)
    print(f"  Wrote {summary_csv_path}")

    # 3. Cluster details JSON
    details_json_path = out_dir / f"{args.module}_ml_cluster_details.json"
    # Prepare JSON-safe version
    json_info = {}
    for cid, info in cluster_info.items():
        json_info[str(cid)] = {k: v for k, v in info.items()}
    with open(details_json_path, "w") as f:
        json.dump(json_info, f, indent=2, default=str)
    print(f"  Wrote {details_json_path}")

    # 4. Scatter plot (if matplotlib available)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.cm as cm

        fig, ax = plt.subplots(figsize=(14, 10))

        # Color by cluster
        unique_labels = sorted(set(cluster_labels))
        cmap = cm.get_cmap("tab20", max(len(unique_labels), 1))

        for idx, cid in enumerate(unique_labels):
            mask = cluster_labels == cid
            color = "gray" if cid == -1 else cmap(idx % 20)
            alpha = 0.3 if cid == -1 else 0.7
            label_str = cluster_info[cid].get("label", str(cid))
            ax.scatter(X_2d[mask, 0], X_2d[mask, 1], c=[color], alpha=alpha,
                       s=20, label=f"C{cid}: {label_str} ({mask.sum()})")

        ax.set_xlabel("UMAP-1")
        ax.set_ylabel("UMAP-2")
        ax.set_title(f"{args.module.upper()} Interface Signal Clusters "
                     f"({n_clusters} clusters, {n_noise} noise)")
        ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=7)
        plt.tight_layout()

        plot_path = out_dir / f"{args.module}_ml_clusters.png"
        fig.savefig(str(plot_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  Wrote {plot_path}")
    except ImportError:
        print("  (matplotlib not available - skipping scatter plot)")

    # Print summary
    print(f"\n{'='*60}")
    print(f"  UNSUPERVISED CLUSTERING SUMMARY for {args.module.upper()}")
    print(f"{'='*60}")
    print(f"  Total signals:    {len(rows)}")
    print(f"  Clusters found:   {n_clusters}")
    print(f"  Noise points:     {n_noise}")
    print(f"  Feature dims:     {X_raw.shape[1]}")
    print(f"  Embedding dims:   {embeddings.shape[1]}")
    print()

    for cid in sorted(cluster_info.keys()):
        info = cluster_info[cid]
        flag = " [NOISE]" if cid == -1 else ""
        print(f"  Cluster {cid:3d}{flag}: {info.get('label','?'):40s}  "
              f"size={info.get('size',0):4d}  "
              f"{info.get('stage_range','')}")
    print()


if __name__ == "__main__":
    main()
