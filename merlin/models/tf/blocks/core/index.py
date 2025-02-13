#
# Copyright (c) 2021, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from typing import Optional, Union

import numpy as np
import tensorflow as tf
from tensorflow.python import to_dlpack

import merlin.io
from merlin.core.dispatch import DataFrameType
from merlin.models.tf.blocks.core.base import Block, PredictionOutput
from merlin.models.tf.utils import tf_utils
from merlin.models.tf.utils.batch_utils import TFModelEncode
from merlin.schema import Tags


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class IndexBlock(Block):
    def __init__(self, values: tf.Tensor, ids: Optional[tf.Tensor] = None, **kwargs):
        super(IndexBlock, self).__init__(**kwargs)
        self.values = values
        self.ids = ids

    @classmethod
    def from_dataset(
        cls, data: merlin.io.Dataset, check_unique_ids: bool = True, **kwargs
    ) -> "IndexBlock":
        if hasattr(data, "to_ddf"):
            data = data.to_ddf()
        if check_unique_ids:
            cls._check_unique_ids(data=data)
        values = tf_utils.df_to_tensor(data)
        ids = tf_utils.df_to_tensor(data.index)

        if len(ids.shape) == 2:
            ids = tf.squeeze(ids)

        return cls(values=values, ids=ids, **kwargs)

    @classmethod
    def from_block(
        cls, block: Block, data: merlin.io.Dataset, id_column: Optional[str] = None, **kwargs
    ) -> "IndexBlock":
        """Build candidates embeddings from applying `block` to a dataset of features `data`.

        Parameters:
        -----------
        block: Block
            The Block that returns embeddings from raw item features.
        data: merlin.io.Dataset
            Dataset containing raw item features.
        id_column: Optional[str]
            The candidates ids column name.
            Note, this will be inferred automatically if the block contains
            a schema with an item-id Tag.
        """
        if not id_column and getattr(block, "schema", None):
            tagged = block.schema.select_by_tag(Tags.ITEM_ID)
            if tagged.column_schemas:
                id_column = tagged.first.name

        model_encode = TFModelEncode(model=block, output_concat_func=np.concatenate)

        data = data.to_ddf()
        block_outputs = data.map_partitions(
            model_encode, filter_input_columns=[id_column]
        ).compute()

        block_outputs.set_index(id_column, inplace=True)

        return cls.from_dataset(block_outputs, **kwargs)

    @staticmethod
    def _check_unique_ids(data: DataFrameType):
        if data.index.to_series().nunique() != data.shape[0]:
            raise ValueError("Please make sure that `data` contains unique indices")

    def update(self, values: tf.Tensor, ids: Optional[tf.Tensor] = None):
        if len(tf.shape(values)) != 2:
            raise ValueError(f"The candidates embeddings tensor must be 2D (got {values.shape}).")
        _ids: tf.Tensor = ids if ids else tf.range(values.shape[0])

        if self.ids:
            self.ids.assign(_ids)
        else:
            self.ids = _ids
        self.values.assign(values)
        return self

    def call(self, inputs: tf.Tensor, **kwargs) -> tf.Tensor:
        return self.values[inputs]

    def to_dataset(self, gpu=True) -> merlin.io.Dataset:
        if gpu:
            import cudf

            df = cudf.from_dlpack(to_dlpack(tf.convert_to_tensor(self.values)))
            df.columns = [str(col) for col in list(df.columns)]
            df.set_index(cudf.RangeIndex(0, self.values.shape[0]))
        else:
            import pandas as pd

            df = pd.DataFrame(self.values.numpy())
            df.columns = [str(col) for col in list(df.columns)]
            df.set_index(pd.RangeIndex(0, self.values.shape[0]))

        return merlin.io.Dataset(df)


@tf.keras.utils.register_keras_serializable(package="merlin_models")
class TopKIndexBlock(IndexBlock):
    """Top-K index to retrieve top-k scores and indices from an item block.

    Parameters:
    -----------
        k: int
            Number of top candidates to retrieve.
        values: tf.Tensor
            The pre-computed embedddings of candidates.
        ids: tf.Tensor
            The candidates ids.
    """

    def __init__(self, k, values: tf.Tensor, ids: Optional[tf.Tensor] = None, **kwargs):
        self._k = k
        super(TopKIndexBlock, self).__init__(values, ids, **kwargs)

    @classmethod
    def from_block(  # type: ignore
        cls,
        block: Block,
        data: merlin.io.Dataset,
        k: int = 20,
        id_column: Optional[str] = None,
        **kwargs,
    ) -> "TopKIndexBlock":
        """
        class method to build candidates embeddings from
        applying `block` to a dataset of features `data`

        Parameters:
        -----------
        block: Block
            The Block that returns embeddings from raw item features.
        output_dim: int
            The output dimension of `block`.
        data: merlin.io.Dataset
            Dataset containing raw item features.
        k: int
            Number of top candidates to retrieve.
            Defaults to 20
        id_column: Optional[str]
            The candidates ids column name.
            Note, this will be inferred automatically if the block contains
            a schema with an item-id Tag.
        """
        return super().from_block(block=block, data=data, id_column=id_column, k=k, **kwargs)

    def call(self, inputs: tf.Tensor, k=None, **kwargs) -> Union[tf.Tensor, tf.Tensor]:
        """
        Compute Top-k scores and related indices from query inputs

        Parameters:
        ----------
        inputs: tf.Tensor
            Tensor of pre-computed query embeddings.
        k: int
            Number of top candidates to retrieve
            Defaults to constructor `_k` parameter.
        Returns
        -------
        top_scores, top_indices: tf.Tensor, tf.Tensor
            2D Tensors with the scores for the top-k candidates and related ids.
        """
        k = k if k is not None else self._k
        scores = tf.matmul(inputs, self.values, transpose_b=True)
        top_scores, top_indices = tf.math.top_k(scores, k=k)
        top_indices = tf.gather(self.ids, top_indices)

        return top_scores, top_indices

    def call_outputs(
        self, outputs: PredictionOutput, training=False, **kwargs
    ) -> "PredictionOutput":
        """
        Retrieve top-k negative scores for evaluation.

        Parameters
        ----------
        predictions: tf.Tensor
            Tensor of pre-computed positive scores.
            If`training=True`, the first column of predictions is expected
            to be positive scores and the remaining sampled negatives are ignored.

        Returns
        -------
        targets, predictions: tf.Tensor, tf.Tensor
            2D Tensors with the one-hot representation of true targets and
            the scores for the top-k implicit negatives.
        """
        targets, predictions = outputs.targets, outputs.predictions
        assert isinstance(predictions, tf.Tensor), "Predictions must be a tensor"
        queries = self.context["query"]
        top_scores, _ = self(queries, k=self._k)
        predictions = tf.expand_dims(predictions[:, 0], -1)
        predictions = tf.concat([predictions, top_scores], axis=-1)
        # Positives in the first column and negatives in the subsequent columns
        targets = tf.concat(
            [
                tf.ones([tf.shape(predictions)[0], 1]),
                tf.zeros([tf.shape(predictions)[0], self._k]),
            ],
            axis=1,
        )
        label_relevant_counts = tf.ones([tf.shape(predictions)[0]])
        return PredictionOutput(predictions, targets, label_relevant_counts)

    def compute_output_shape(self, input_shape):
        batch_size = input_shape[0]
        return tf.TensorShape((batch_size, self._k)), tf.TensorShape((batch_size, self._k))
