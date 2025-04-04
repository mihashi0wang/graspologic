# Copyright (c) Microsoft Corporation and contributors.
# Licensed under the MIT License.

import warnings
from typing import Optional, Union

import numpy as np
from beartype import beartype
from scipy.sparse import csr_array, hstack, isspmatrix_csr, vstack

from graspologic.types import List

from ..types import AdjacencyMatrix, GraphRepresentation
from ..utils import average_matrices, is_fully_connected, to_laplacian
from .base import BaseEmbedMulti
from .svd import SvdAlgorithmType


@beartype
def _get_omnibus_matrix_sparse(matrices: List[csr_array]) -> csr_array:
    """
    Generate the omnibus matrix from a list of sparse adjacency matrices as described by 'A central limit theorem
    for an omnibus embedding of random dot product graphs.'

    Given an iterable of matrices a, b, ... n then the omnibus matrix is defined as::

        [[           a, .5 * (a + b), ..., .5 * (a + n)],
         [.5 * (b + a),            b, ..., .5 * (b + n)],
         [         ...,          ..., ...,          ...],
         [.5 * (n + a),  .5 * (n + b, ...,            n]
        ]
    """

    rows = []

    # Iterate over each column
    for column_index, column_matrix in enumerate(matrices):
        current_row = []

        for row_index, row_matrix in enumerate(matrices):
            if row_index == column_index:
                # we are on the diagonal, we do not need to perform any calculation and instead add the current matrix
                # to the current_row
                current_row.append(column_matrix)
            else:
                # otherwise we are not on the diagonal and we average the current_matrix with the matrix at row_index
                # and add that to our current_row
                matrices_averaged = (column_matrix + row_matrix) * 0.5
                current_row.append(matrices_averaged)

        # an entire row has been generated, we will create a horizontal stack of each matrix in the row completing the
        # row
        rows.append(hstack(current_row))

    return csr_array(vstack(rows, format="csr"))


def _get_laplacian_matrices(
    graphs: Union[np.ndarray, List[GraphRepresentation]],
) -> Union[np.ndarray, List[np.ndarray]]:
    """
    Helper function to convert graph adjacency matrices to graph Laplacian

    Parameters
    ----------
    graphs : list
        List of array-like with shapes (n_vertices, n_vertices).

    Returns
    -------
    out : list
        List of array-like with shapes (n_vertices, n_vertices).
    """
    out: Union[np.ndarray, List[np.ndarray]]
    if isinstance(graphs, list):
        out = [to_laplacian(g) for g in graphs]
    elif isinstance(graphs, np.ndarray):
        # Copying is necessary to not overwrite input array
        out = np.array([to_laplacian(graphs[i]) for i in range(len(graphs))])

    return out


def _get_omni_matrix(
    graphs: Union[AdjacencyMatrix, List[AdjacencyMatrix]],
) -> np.ndarray:
    """
    Helper function for creating the omnibus matrix.

    Parameters
    ----------
    graphs : list
        List of array-like with shapes (n_vertices, n_vertices).

    Returns
    -------
    out : 2d-array
        Array of shape (n_vertices * n_graphs, n_vertices * n_graphs)
    """
    if isinstance(graphs[0], csr_array):
        return _get_omnibus_matrix_sparse(graphs)  # type: ignore

    shape = graphs[0].shape
    n = shape[0]  # number of vertices
    m = len(graphs)  # number of graphs

    graphs_array = np.asarray(graphs)
    A  = np.atleast_3d(graphs_array)
    #np.array(graphs, copy=False, ndmin=3)

    # Do some numpy broadcasting magic.
    # We do sum in 4d arrays and reduce to 2d array.
    # Super fast and efficient
    out = (A[:, :, None, :] + A.transpose(1, 0, 2)[None, :, :, :]).reshape(n * m, -1)

    # Averaging
    out /= 2

    return out


class OmnibusEmbed(BaseEmbedMulti):
    r"""
    Omnibus embedding of arbitrary number of input graphs with matched vertex
    sets.

    Given :math:`A_1, A_2, ..., A_m` a collection of (possibly weighted) adjacency
    matrices of a collection :math:`m` undirected graphs with matched vertices.
    Then the :math:`(mn \times mn)` omnibus matrix, :math:`M`, has the subgraph where
    :math:`M_{ij} = \frac{1}{2}(A_i + A_j)`. The omnibus matrix is then embedded
    using adjacency spectral embedding.

    Read more in the `Omnibus Embedding for Multiple Graphs Tutorial
    <https://microsoft.github.io/graspologic/tutorials/embedding/Omnibus.html>`_

    Parameters
    ----------
    n_components : int or None, default = None
        Desired dimensionality of output data. If "full",
        ``n_components`` must be ``<= min(X.shape)``. Otherwise, ``n_components`` must be
        ``< min(X.shape)``. If None, then optimal dimensions will be chosen by
        :func:`~graspologic.embed.select_dimension` using ``n_elbows`` argument.

    n_elbows : int, optional, default: 2
        If ``n_components`` is None, then compute the optimal embedding dimension using
        :func:`~graspologic.embed.select_dimension`. Otherwise, ignored.

    algorithm : {'randomized' (default), 'full', 'truncated'}, optional
        SVD solver to use:

        - 'randomized'
            Computes randomized svd using
            :func:`sklearn.utils.extmath.randomized_svd`
        - 'full'
            Computes full svd using :func:`scipy.linalg.svd`
        - 'truncated'
            Computes truncated svd using :func:`scipy.sparse.linalg.svds`

    n_iter : int, optional (default = 5)
        Number of iterations for randomized SVD solver. Not used by 'full' or
        'truncated'. The default is larger than the default in randomized_svd
        to handle sparse matrices that may have large slowly decaying spectrum.

    check_lcc : bool , optional (defult = True)
        Whether to check if the average of all input graphs are connected. May result
        in non-optimal results if the average graph is unconnected. If True and average
        graph is unconnected, a UserWarning is thrown.

    diag_aug : bool, optional (default = True)
        Whether to replace the main diagonal of each adjacency matrices with
        a vector corresponding to the degree (or sum of edge weights for a
        weighted network) before embedding.

    concat : bool, optional (default = False)
        If graph(s) are directed, whether to concatenate each graph's left and right (out and in) latent positions
        along axis 1.

    svd_seed : int or None (default = ``None``)
        Only applicable for ``algorithm="randomized"``; allows you to seed the
        randomized svd solver for deterministic, albeit pseudo-randomized behavior.

    lse : bool, optional (default = False)
        Whether to construct the Omni matrix use the laplacian matrices
        of the graphs and embed the Omni matrix with LSE

    Attributes
    ----------
    n_graphs_ : int
        Number of graphs

    n_vertices_ : int
        Number of vertices in each graph

    latent_left_ : array, shape (n_graphs, n_vertices, n_components)
        Estimated left latent positions of the graph.

    latent_right_ : array, shape (n_graphs, n_vertices, n_components), or None
        Only computed when the graph is directed, or adjacency matrix is
        asymmetric. Estimated right latent positions of the graph. Otherwise,
        None.

    singular_values_ : array, shape (n_components)
        Singular values associated with the latent position matrices.


    See Also
    --------
    graspologic.embed.select_svd
    graspologic.embed.select_dimension

    References
    ----------
    .. [1] Levin, K., Athreya, A., Tang, M., Lyzinski, V., & Priebe, C. E. (2017,
       November). A central limit theorem for an omnibus embedding of multiple random
       dot product graphs. In Data Mining Workshops (ICDMW), 2017 IEEE International
       Conference on (pp. 964-967). IEEE.
    """

    def __init__(
        self,
        n_components: Optional[int] = None,
        n_elbows: Optional[int] = 2,
        algorithm: SvdAlgorithmType = "randomized",
        n_iter: int = 5,
        check_lcc: bool = True,
        diag_aug: bool = True,
        concat: bool = False,
        svd_seed: Optional[int] = None,
        lse: bool = False,
    ):
        super().__init__(
            n_components=n_components,
            n_elbows=n_elbows,
            algorithm=algorithm,
            n_iter=n_iter,
            check_lcc=check_lcc,
            diag_aug=diag_aug,
            concat=concat,
            svd_seed=svd_seed,
        )
        self.lse = lse

    def fit(self, graphs, y=None):  # type: ignore
        """
        Fit the model with graphs.

        Parameters
        ----------
        graphs : list of nx.Graph or ndarray, or csr_array
            If list of nx.Graph, each Graph must contain same number of nodes.
            If list of ndarray, each array must have shape (n_vertices, n_vertices).
            If ndarray, then array must have shape (n_graphs, n_vertices, n_vertices).

        Returns
        -------
        self : object
            Returns an instance of self.
        """
        graphs = self._check_input_graphs(graphs)

        # Check if Abar is connected
        if self.check_lcc:
            if not is_fully_connected(average_matrices(graphs)):
                msg = (
                    "Input graphs are not fully connected. Results may not"
                    + "be optimal. You can compute the largest connected component by"
                    + "using ``graspologic.utils.multigraph_lcc_union``."
                )
                warnings.warn(msg, UserWarning)

        # Diag augment
        if self.diag_aug:
            graphs = self._diag_aug(graphs)

        # Laplacian transform
        if self.lse:
            graphs = _get_laplacian_matrices(graphs)

        # Create omni matrix
        omni_matrix = _get_omni_matrix(graphs)

        # Embed
        self._reduce_dim(omni_matrix)

        # Reshape to tensor
        self.latent_left_ = self.latent_left_.reshape(
            self.n_graphs_, self.n_vertices_, -1
        )
        if self.latent_right_ is not None:
            self.latent_right_ = self.latent_right_.reshape(
                self.n_graphs_, self.n_vertices_, -1
            )

        return self

    def fit_transform(self, graphs, y=None):  # type: ignore
        """
        Fit the model with graphs and apply the embedding on graphs.
        n_components is either automatically determined or based on user input.

        Parameters
        ----------
        graphs : list of nx.Graph or ndarray, or ndarray
            If list of nx.Graph, each Graph must contain same number of nodes.
            If list of ndarray, each array must have shape (n_vertices, n_vertices).
            If ndarray, then array must have shape (n_graphs, n_vertices, n_vertices).

        Returns
        -------
        out : np.ndarray or length 2 tuple of np.ndarray.
            If input graphs were symmetric, ndarray of shape (n_graphs, n_vertices, n_components).
            If graphs were directed and ``concat`` is False, returns tuple of two arrays (same shape as above).
            The first corresponds to the left latent positions, and the second to the right latent positions.
            If graphs were directed and ``concat`` is True, left and right (out and in) latent positions are concatenated.
            In this case one tensor of shape (n_graphs, n_vertices, 2*n_components) is returned.
        """
        return self._fit_transform(graphs)
