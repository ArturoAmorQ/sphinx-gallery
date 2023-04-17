# -*- coding: utf-8 -*-
# Author: Arturo Amor
# License: 3-clause BSD
"""
Recommendation system generator
===============================

Generate recommendations based on TF-IDF representation and a KNN model.
"""
import numbers
# import pickle
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import norm

from .backreferences import (
    _thumbnail_div,
    THUMBNAIL_PARENT_DIV,
    THUMBNAIL_PARENT_DIV_CLOSE,
)
from .py_source_parser import split_code_and_text_blocks
from .gen_rst import extract_intro_and_title


class ExampleRecommender:
    """Compute content-based KNN-TF-IFD recommendation system.

    Parameters
    ----------
    n_examples : int, default=5
        Number of most relevant examples to display.

    tokenizer : {"raw", "backrefs"}, default="raw"
        The type of tokenizer to use. If "raw", the raw text will be used as
        tokens. If "backrefs", the list of sphinx-gallery backreferences will
        be used as tokens.

    Attributes
    ----------
    file_names_ : list of str
        The list of file names used for computing the similarity matrix.
        The recommended examples will be chosen among this list.

    similarity_matrix_ : sparse matrix
        Fitted matrix of pairwise cosine similarities.
    """

    def __init__(self, *, n_examples=5, tokenizer="raw"):
        self.n_examples = n_examples
        self.tokenizer = tokenizer

    @staticmethod
    def token_freqs(doc):
        """Extract a dict mapping raw tokens from doc to their occurrences."""
        token_generator = (tok.lower() for tok in re.findall(r"\w+", doc))
        return dict_freqs(token_generator)

    @staticmethod
    def dict_freqs(doc):
        """Extract a dict mapping list of tokens to their occurrences."""
        freq = defaultdict(int)
        for tok in doc:
            freq[tok] += 1
        return freq

    @staticmethod
    def dict_vectorizer(data):
        """Convert a dictionary of feature arrays into a sparse matrix.

        Parameters
        ----------
        data : list of dict
            An iterable of dictionaries of feature arrays, where each key
            corresponds to a feature name, and each value is an array of feature
            values.

        Returns
        -------
        X : sparse matrix
            A sparse matrix in CSR format of shape (n_samples, n_features) where
            n_samples is the number of samples in the dataset and n_features is the
            total number of features across all samples.
        """
        feature_names = []
        all_values = defaultdict(list)
        for row in data:
            for feature_name, feature_value in row.items():
                feature_names.append(feature_name)
                all_values[feature_name].append(feature_value)

        feature_names = sorted(set(feature_names))
        data, indices, indptr = [], [], [0]
        for row in data:
            for j, feature_name in enumerate(feature_names):
                if feature_name in row:
                    feature_value = row[feature_name]
                    data.append(feature_value)
                    indices.append(j)
            indptr.append(len(indices))
        X = sparse.csr_matrix(
            (data, indices, indptr), shape=(len(indptr) - 1, len(feature_names))
        )
        return X

    @staticmethod
    def tfidf_transformer(X):
        """Transform a term frequency matrix into a term frequency-inverse document
        frequency (TF-IDF) matrix.

        Parameters
        ----------
        X : {ndarray, sparse matrix} of shape (n_samples, n_features)
            A term frequency matrix.

        Returns
        -------
        X_tfidf : {ndarray, sparse matrix} of shape (n_samples, n_features)
            A tf-idf matrix of the same shape as X.
        """
        if not sparse.issparse(X):
            X = sparse.csr_matrix(X, dtype=X.dtype)

        n_samples, n_features = X.shape

        # Count the number of non-zero values for each feature in sparse X
        if sparse.isspmatrix_csr(X):
            df = np.bincount(X.indices, minlength=n_features)
        else:
            df = np.diff(X.indptr)
        df = df.astype(X.dtype, copy=False)
        # perform idf smoothing
        df += 1
        n_samples += 1
        idf = np.log(n_samples / df) + 1

        idf_diag = sparse.diags(
            idf,
            offsets=0,
            shape=(n_features, n_features),
            format="csr",
            dtype=X.dtype,
        )
        X_tfidf = X * idf_diag
        X_tfidf = (X_tfidf.T / norm(X_tfidf, axis=1)).T
        X_tfidf = sparse.csr_matrix(X_tfidf, dtype=X.dtype)

        return X_tfidf

    @staticmethod
    def cosine_similarity(X, Y=None, dense_output=True):
        """
        Compute the cosine similarity between two vectors X and Y.

        Parameters
        ----------
        X : {ndarray, sparse matrix} of shape (n_samples_X, n_features)
            Input data.

        Y : {ndarray, sparse matrix} of shape (n_samples_Y, n_features), default=None
            Input data. If `None`, the output will be the pairwise
            similarities between all samples in `X`.

        dense_output : bool, default=True
            Whether to return dense output even when the input is sparse. If
            `False`, the output is sparse if both input arrays are sparse.

        Returns
        -------
        cosine_similarity : ndarray of shape (n_samples_X, n_samples_Y)
            Cosine similarity matrix.
        """

        if Y is X or Y is None:
            Y = X

        X_normalized = X / norm(X)
        if X is Y:
            Y_normalized = X_normalized
        else:
            Y_normalized = Y / norm(Y)

        X_normalized = sparse.csr_matrix(X_normalized, dtype=X.dtype)
        similarity = X_normalized @ Y_normalized.T

        if dense_output:
            return similarity.toarray()
        return similarity

    def fit(self, file_names):
        """
        Compute the similarity matrix of a group of documents.

        Parameters
        ----------
        file_names : list or generator of file names.

        Returns
        -------
        self : object
            Fitted recommender.
        """
        n_examples = self.n_examples
        known_tokenizers = {"raw", "backrefs"}
        if self.tokenizer not in known_tokenizers:
            raise ValueError(
                f"Unknown tokenizer {self.tokenizer}. "
                f"Expected one of {known_tokenizers}."
            )
        if not isinstance(n_examples, numbers.Integral):
            raise ValueError("n_examples must be an integer")
        elif n_examples < 1:
            raise ValueError("n_examples must be strictly positive")

        if self.tokenizer == "raw":
            frequency_func = self.token_freqs
            counts_matrix = self.dict_vectorizer(
                [frequency_func(Path(fname).read_text()) for fname in file_names]
            )
        else:  # self.tokenizer == "backrefs"
            frequency_func = self.dict_freqs
        #     backrefs_list = []
        #     for fname in file_names:
        #         pickle_file = fname[:-3] + "_codeobj.pickle"
        #         try:
        #             with open(pickle_file, "rb") as f:
        #                 names = pickle.load(f)
        #             back_references = [
        #                 name.split("_codeobj")[0] for name in names.keys()
        #             ]
        #         except:
        #             back_references = []
        #             continue
        #         backrefs_list.append(back_references)
        #     counts_matrix = dict_vectorizer(
        #         [frequency_func(backref) for backref in backrefs_list]
        #     )

        tfidf_matrix = self.tfidf_transformer(counts_matrix)
        self.similarity_matrix_ = self.cosine_similarity(
            self.tfidf_transformer(counts_matrix)
        )
        self.file_names_ = file_names
        return self


    def predict(self, file_name):
        """Compute the most `n_examples` similar documents to the query.

        Parameters
        ----------
        file_name : str
            Name of the file corresponding to the query index `item_id`.

        Returns
        -------
        recommendations : list of str
            Name of the files most similar to the query.
        """
        item_id = self.file_names_.index(file_name)
        similar_items = list(enumerate(self.similarity_matrix_[item_id]))
        sorted_items = sorted(similar_items, key=lambda x: x[1], reverse=True)

        # Get the top k items similar to item_id
        top_k_items = [index for index, _ in sorted_items[1 : self.n_examples + 1]]
        recommendations = [self.file_names_[index] for index in top_k_items]
        return recommendations


def _write_recommendations(recommender, fname, gallery_conf):
    """Generate `.recommendations` RST file for a given example.

    Parameters
    ----------
    recommender : ExampleRecommender
        Instance of a fitted ExampleRecommender.

    fname : str
        Path to the example file.

    gallery_conf : dict
        Configuration dictionary for the sphinx-gallery extension.
    """
    path_fname = Path(fname)
    recommendation_fname = f"{path_fname.parent / path_fname.stem}.recommendations"
    recommended_examples = recommender.predict(fname)

    with open(recommendation_fname, "w", encoding="utf-8") as ex_file:
        ex_file.write("\n\n.. rubric:: Related examples\n")
        ex_file.write(THUMBNAIL_PARENT_DIV)
        for example_fname in recommended_examples:
            example_path = Path(example_fname)
            _, script_blocks = split_code_and_text_blocks(
                example_fname, return_node=False
            )
            intro, title = extract_intro_and_title(fname, script_blocks[0][1])
            ex_file.write(
                _thumbnail_div(
                    example_path.parent,
                    gallery_conf["src_dir"],
                    example_path.name,
                    intro,
                    title,
                    is_backref=True,
                )
            )
        ex_file.write(THUMBNAIL_PARENT_DIV_CLOSE)
