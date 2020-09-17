# Copyright 2019, The TensorFlow Federated Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Federated EMNIST character recognition library using TFF."""

import functools
from typing import Any, Callable, Optional

from absl import logging
import tensorflow as tf
import tensorflow_federated as tff

from tensorflow_federated.python.research.utils import training_loop
from tensorflow_federated.python.research.utils import training_utils
from tensorflow_federated.python.research.utils.datasets import emnist_dataset
from tensorflow_federated.python.research.utils.models import emnist_models

EMNIST_MODELS = ['cnn', '2nn']


def run_federated(
    iterative_process_builder: Callable[..., tff.templates.IterativeProcess],
    assign_weights_fn: Callable[[Any, tf.keras.Model], None],
    client_epochs_per_round: int,
    client_batch_size: int,
    clients_per_round: int,
    client_datasets_random_seed: Optional[int] = None,
    emnist_model: Optional[str] = 'cnn'):
  """Runs an iterative process on the EMNIST character recognition task.

  This method will load and pre-process dataset and construct a model used for
  the task. It then uses `iterative_process_builder` to create an iterative
  process that it applies to the task, using
  `tensorflow_federated.python.research.utils.training_loop`.

   We assume that the iterative process has the following functional type
   signatures:

    *   `initialize`: `( -> S@SERVER)` where `S` represents the server state.
    *   `next`: `<S@SERVER, {B*}@CLIENTS> -> <S@SERVER, T@SERVER>` where `S`
        represents the server state, `{B*}` represents the client datasets,
        and `T` represents a python `Mapping` object.

  Args:
    iterative_process_builder: A function that accepts a no-arg `model_fn`, and
      returns a `tff.templates.IterativeProcess`. The `model_fn` must return a
      `tff.learning.Model`.
    assign_weights_fn: A function that accepts the server state `S` and a
      `tf.keras.Model`, and updates the weights in the Keras model. This is used
      to do evaluation using Keras.
    client_epochs_per_round: An integer representing the number of epochs of
      training performed per client in each training round.
    client_batch_size: An integer representing the batch size used on clients.
    clients_per_round: An integer representing the number of clients
      participating in each round.
    client_datasets_random_seed: An optional int used to seed which clients are
      sampled at each round. If `None`, no seed is used.
    emnist_model: A string specifying the model used for character recognition.
      Can be one of `cnn` and `2nn`, corresponding to a CNN model and a densely
      connected 2-layer model (respectively).
  """

  emnist_train, emnist_test = emnist_dataset.get_emnist_datasets(
      client_batch_size, client_epochs_per_round, only_digits=False)

  input_spec = emnist_train.create_tf_dataset_for_client(
      emnist_train.client_ids[0]).element_spec

  if emnist_model == 'cnn':
    model_builder = functools.partial(
        emnist_models.create_conv_dropout_model, only_digits=False)
  elif emnist_model == '2nn':
    model_builder = functools.partial(
        emnist_models.create_two_hidden_layer_model, only_digits=False)
  else:
    raise ValueError(
        'Cannot handle model flag [{!s}], must be one of {!s}.'.format(
            emnist_model, EMNIST_MODELS))

  loss_builder = tf.keras.losses.SparseCategoricalCrossentropy
  metrics_builder = lambda: [tf.keras.metrics.SparseCategoricalAccuracy()]

  def tff_model_fn() -> tff.learning.Model:
    return tff.learning.from_keras_model(
        keras_model=model_builder(),
        input_spec=input_spec,
        loss=loss_builder(),
        metrics=metrics_builder())

  training_process = iterative_process_builder(tff_model_fn)

  client_datasets_fn = training_utils.build_client_datasets_fn(
      train_dataset=emnist_train,
      train_clients_per_round=clients_per_round,
      random_seed=client_datasets_random_seed)

  evaluate_fn = training_utils.build_evaluate_fn(
      eval_dataset=emnist_test,
      model_builder=model_builder,
      loss_builder=loss_builder,
      metrics_builder=metrics_builder,
      assign_weights_to_keras_model=assign_weights_fn)

  logging.info('Training model:')
  logging.info(model_builder().summary())

  training_loop.run(
      iterative_process=training_process,
      client_datasets_fn=client_datasets_fn,
      validation_fn=evaluate_fn,
      test_fn=evaluate_fn)
