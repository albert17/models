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
import logging
from typing import Any, Callable, Dict, Optional

from merlin.models.tf.blocks.core.transformations import RenameFeatures
from merlin.models.tf.features.embedding import EmbeddingFeatures, EmbeddingOptions
from merlin.schema import Schema, Tags

LOG = logging.getLogger("merlin_models")


def MatrixFactorizationBlock(
    schema: Schema,
    dim: int,
    query_id_tag=Tags.USER_ID,
    item_id_tag=Tags.ITEM_ID,
    embeddings_initializers: Optional[Dict[str, Callable[[Any], None]]] = None,
    **kwargs,
):
    query_item_schema = schema.select_by_tag(query_id_tag) + schema.select_by_tag(item_id_tag)
    embedding_options = EmbeddingOptions(
        embedding_dim_default=dim, embeddings_initializers=embeddings_initializers
    )

    rename_features = RenameFeatures({query_id_tag: "query", item_id_tag: "item"}, schema=schema)
    post = kwargs.pop("post", None)
    if post:
        post = rename_features.connect(post)
    else:
        post = rename_features

    matrix_factorization = EmbeddingFeatures.from_schema(
        query_item_schema, post=post, options=embedding_options, **kwargs
    )

    return matrix_factorization
