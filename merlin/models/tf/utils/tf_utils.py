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
from typing import Union

import numpy as np
import tensorflow as tf
from packaging import version

from merlin.models.tf.typing import TabularData

if version.parse(tf.__version__) < version.parse("2.3.0"):
    try:
        from tfdlpack import from_dlpack
    except ModuleNotFoundError as e:
        message = "If using TensorFlow < 2.3.0, you must install tfdlpack-gpu extension library"
        raise ModuleNotFoundError(message) from e

else:
    from tensorflow.experimental.dlpack import from_dlpack


def get_output_sizes_from_schema(schema, batch_size=0, max_sequence_length=None):
    sizes = {}
    for feature in schema:
        name = feature.name
        if feature.is_list:
            sizes[name] = tf.TensorShape(
                [
                    batch_size,
                    max_sequence_length if max_sequence_length else feature.value_count.max,
                ]
            )
        elif feature.HasField("shape"):
            sizes[name] = tf.TensorShape([batch_size] + [d.size for d in feature.shape.dim])
        else:
            sizes[name] = tf.TensorShape([batch_size, 1])

    return sizes


def calculate_batch_size_from_input_shapes(input_shapes):
    values = []

    for val in input_shapes.values():
        if isinstance(val, tuple) and isinstance(val[0], tf.TensorShape):
            values.append(val[1])
        else:
            values.append(val)

    return values[0][0]


def maybe_serialize_keras_objects(
    self,
    config,
    maybe_serialize_keys,
):
    for key in maybe_serialize_keys:
        maybe_value = getattr(self, key, None)
        if maybe_value:
            if isinstance(maybe_value, dict):
                config[key] = {
                    k: tf.keras.utils.serialize_keras_object(v) for k, v in maybe_value.items()
                }
            elif isinstance(maybe_value, (list, tuple)):
                config[key] = [tf.keras.utils.serialize_keras_object(v) for v in maybe_value]
            else:
                config[key] = tf.keras.utils.serialize_keras_object(maybe_value)

    return config


def maybe_deserialize_keras_objects(
    config, to_deserialize, deserialize_fn=tf.keras.utils.deserialize_keras_object
):
    if isinstance(to_deserialize, list):
        to_deserialize = {k: deserialize_fn for k in to_deserialize}

    custom_objects = {}

    for key, fn in to_deserialize.items():
        maybe_val = config.get(key, None)
        if maybe_val:
            if isinstance(maybe_val, list):
                config[key] = [fn(v, custom_objects=custom_objects) for v in maybe_val]
            else:
                config[key] = fn(maybe_val, custom_objects=custom_objects)

    return config


def extract_topk(k, predictions, labels):
    # Computes the number of relevant items per row (before extracting only the top-k)
    label_relevant_counts = tf.reduce_sum(labels, axis=-1)
    # Limits k to the number of prediction scores
    k = tf.minimum(k, tf.shape(predictions)[-1])
    topk_predictions, topk_indices = tf.math.top_k(predictions, k)
    topk_labels = gather_torch_like(labels, topk_indices, k)
    return topk_predictions, topk_labels, label_relevant_counts


def transform_label_to_onehot(labels, vocab_size):
    return tf.one_hot(tf.reshape(labels, (-1,)), vocab_size)


def create_output_placeholder(scores, ks):
    return tf.Variable(tf.zeros([tf.shape(scores)[0], len(ks)], tf.float32))


def gather_torch_like(labels, indices, max_k):

    row_idxs = tf.repeat(tf.range(tf.shape(labels)[0]), max_k)
    col_idx = tf.reshape(indices, tf.shape(row_idxs))
    all_indices = tf.transpose(tf.stack([row_idxs, col_idx]))

    labels = tf.reshape(tf.gather_nd(labels, all_indices), (tf.shape(labels)[0], max_k))
    return labels


def batch_ref(inputs: Union[tf.Tensor, TabularData]):
    """Get hash-code of a tensor or a dictionary of tensors."""

    if isinstance(inputs, tf.Tensor):
        return hash(inputs.ref())

    refs = []
    keys = sorted(inputs.keys())
    for key in keys:
        refs.append(inputs[key].ref())

    return hash(tuple(refs))


def pack_df(gdf):
    if isinstance(gdf, np.ndarray):
        return gdf
    elif hasattr(gdf, "to_dlpack") and callable(getattr(gdf, "to_dlpack")):
        return gdf.to_dlpack()
    elif hasattr(gdf, "to_numpy") and callable(getattr(gdf, "to_numpy")):
        gdf = gdf.to_numpy()
        if isinstance(gdf[0], list):
            gdf = np.stack(gdf)
        return gdf
    return gdf.toDlpack()


def unpack_df(gdf):
    if hasattr(gdf, "shape"):
        return tf.convert_to_tensor(gdf)
    return from_dlpack(gdf)


def df_to_tensor(gdf, dtype=None):
    if gdf.empty:
        return

    # checks necessary because of this bug
    # https://github.com/tensorflow/tensorflow/issues/42660
    if len(gdf.shape) == 1 or gdf.shape[1] == 1:
        dlpack = pack_df(gdf)
    elif gdf.shape[0] == 1:
        dlpack = pack_df(gdf.values[0])
    else:
        dlpack = pack_df(gdf.values.T)
    # catch error caused by tf eager context
    # not being initialized

    try:
        x = unpack_df(dlpack)
    except AssertionError:
        tf.random.uniform((1,))
        x = unpack_df(dlpack)
    # if rank is already two it is  already in list format
    if gdf.shape[0] == 1 and not tf.rank(x) == 2:
        # batch size 1 so got squashed to a vector
        x = tf.expand_dims(x, 0)
    elif len(gdf.shape) == 1 or len(x.shape) == 1:
        # sort of a generic check for any other
        # len(shape)==1 case, could probably
        # be more specific
        x = tf.expand_dims(x, -1)
    elif gdf.shape[1] > 1:
        # matrix which means we had to transpose
        # for the bug above, so untranspose
        x = tf.transpose(x)
    return x
