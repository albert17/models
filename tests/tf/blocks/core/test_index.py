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

import pytest

import merlin.models.tf as ml
from merlin.io.dataset import Dataset
from merlin.models.data.synthetic import SyntheticData
from merlin.schema import Tags


def test_topk_index(ecommerce_data: SyntheticData):
    model: ml.RetrievalModel = ml.TwoTowerModel(
        ecommerce_data.schema, query_tower=ml.MLPBlock([64, 128])
    )
    model.compile(run_eagerly=True, optimizer="adam")
    dataset = ecommerce_data.tf_dataloader(batch_size=50)
    model.fit(dataset, epochs=1)

    item_features = ecommerce_data.schema.select_by_tag(Tags.ITEM).column_names
    item_dataset = ecommerce_data.dataframe[item_features].drop_duplicates()
    item_dataset = Dataset(item_dataset)

    recommender = model.to_top_k_recommender(item_dataset, k=20)

    batch = next(iter(dataset))[0]
    _, top_indices = recommender(batch)
    assert top_indices.shape[-1] == 20
    _, top_indices = recommender(batch, k=10)
    assert top_indices.shape[-1] == 10


def test_topk_index_duplicate_indices(ecommerce_data: SyntheticData):
    model: ml.RetrievalModel = ml.TwoTowerModel(
        ecommerce_data.schema, query_tower=ml.MLPBlock([64, 128])
    )
    model.compile(run_eagerly=True, optimizer="adam")
    dataset = ecommerce_data.tf_dataloader(batch_size=50)
    model.fit(dataset, epochs=1)
    item_features = ecommerce_data.schema.select_by_tag(Tags.ITEM).column_names
    item_dataset = ecommerce_data.dataframe[item_features]
    item_dataset = Dataset(item_dataset)

    with pytest.raises(ValueError) as excinfo:
        _ = model.to_top_k_recommender(item_dataset, k=20)
    assert "Please make sure that `data` contains unique indices" in str(excinfo.value)
