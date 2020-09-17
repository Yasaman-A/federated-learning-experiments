# Copyright 2020, The TensorFlow Federated Authors.
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
"""Runs federated training on various tasks using a generalized form of FedAvg.

Specifically, we create (according to flags) an iterative processes that allows
for client and server learning rate schedules, as well as various client and
server optimization methods. For more details on the learning rate scheduling
and optimization methods, see `shared/optimizer_utils.py`. For details on the
iterative process, see `shared/fed_avg_schedule.py`.
"""

import collections
from typing import Any, Callable, Optional

from absl import app
from absl import flags
import tensorflow as tf
import tensorflow_federated as tff

from tensorflow_federated.python.research.optimization.cifar100 import federated_cifar100
from tensorflow_federated.python.research.optimization.emnist import federated_emnist
from tensorflow_federated.python.research.optimization.emnist_ae import federated_emnist_ae
from tensorflow_federated.python.research.optimization.shakespeare import federated_shakespeare
from tensorflow_federated.python.research.optimization.shared import fed_avg_schedule
from tensorflow_federated.python.research.optimization.shared import optimizer_utils
from tensorflow_federated.python.research.optimization.stackoverflow import federated_stackoverflow
from tensorflow_federated.python.research.optimization.stackoverflow_lr import federated_stackoverflow_lr
from tensorflow_federated.python.research.utils import utils_impl

_SUPPORTED_TASKS = [
    'cifar100', 'emnist_cr', 'emnist_ae', 'shakespeare', 'stackoverflow_nwp',
    'stackoverflow_lr'
]

# Defining optimizer flags
with utils_impl.record_hparam_flags():
  optimizer_utils.define_optimizer_flags('client')
  optimizer_utils.define_optimizer_flags('server')
  optimizer_utils.define_lr_schedule_flags('client')
  optimizer_utils.define_lr_schedule_flags('server')

with utils_impl.record_hparam_flags():
  flags.DEFINE_enum('task', None, _SUPPORTED_TASKS,
                    'Which task to perform federated training on.')

  # Training hyperparameters
  flags.DEFINE_integer('client_epochs_per_round', 1,
                       'Number of epochs in the client to take per round.')
  flags.DEFINE_integer('client_batch_size', 20, 'Batch size on the clients.')
  flags.DEFINE_integer('clients_per_round', 10,
                       'How many clients to sample per round.')
  flags.DEFINE_integer('client_datasets_random_seed', 1,
                       'Random seed for client sampling.')

  # CIFAR-100 flags
  flags.DEFINE_integer('cifar100_crop_size', 24, 'The height and width of '
                       'images after preprocessing.')

  # EMNIST CR flags
  flags.DEFINE_enum(
      'emnist_cr_model', 'cnn', ['cnn', '2nn'], 'Which model to '
      'use. This can be a convolutional model (cnn) or a two '
      'hidden-layer densely connected network (2nn).')

  # Shakespeare flags
  flags.DEFINE_integer(
      'shakespeare_sequence_length', 80,
      'Length of character sequences to use for the RNN model.')

  # Stack Overflow NWP flags
  flags.DEFINE_integer('so_nwp_vocab_size', 10000, 'Size of vocab to use.')
  flags.DEFINE_integer('so_nwp_num_oov_buckets', 1,
                       'Number of out of vocabulary buckets.')
  flags.DEFINE_integer('so_nwp_sequence_length', 20,
                       'Max sequence length to use.')
  flags.DEFINE_integer('so_nwp_max_elements_per_user', 1000, 'Max number of '
                       'training sentences to use per user.')
  flags.DEFINE_integer(
      'so_nwp_num_validation_examples', 10000, 'Number of examples '
      'to use from test set for per-round validation.')
  flags.DEFINE_integer('so_nwp_embedding_size', 96,
                       'Dimension of word embedding to use.')
  flags.DEFINE_integer('so_nwp_latent_size', 670,
                       'Dimension of latent size to use in recurrent cell')
  flags.DEFINE_integer('so_nwp_num_layers', 1,
                       'Number of stacked recurrent layers to use.')
  flags.DEFINE_boolean(
      'so_nwp_shared_embedding', False,
      'Boolean indicating whether to tie input and output embeddings.')

  # Stack Overflow LR flags
  flags.DEFINE_integer('so_lr_vocab_tokens_size', 10000,
                       'Vocab tokens size used.')
  flags.DEFINE_integer('so_lr_vocab_tags_size', 500, 'Vocab tags size used.')
  flags.DEFINE_integer(
      'so_lr_num_validation_examples', 10000, 'Number of examples '
      'to use from test set for per-round validation.')
  flags.DEFINE_integer('so_lr_max_elements_per_user', 1000,
                       'Max number of training '
                       'sentences to use per user.')

FLAGS = flags.FLAGS


def main(argv):
  if len(argv) > 1:
    raise app.UsageError('Expected no command-line arguments, '
                         'got: {}'.format(argv))

  client_optimizer_fn = optimizer_utils.create_optimizer_fn_from_flags('client')
  server_optimizer_fn = optimizer_utils.create_optimizer_fn_from_flags('server')

  client_lr_schedule = optimizer_utils.create_lr_schedule_from_flags('client')
  server_lr_schedule = optimizer_utils.create_lr_schedule_from_flags('server')

  def iterative_process_builder(
      model_fn: Callable[[], tff.learning.Model],
      client_weight_fn: Optional[Callable[[Any], tf.Tensor]] = None,
      dataset_preprocess_comp: Optional[tff.Computation] = None,
  ) -> tff.templates.IterativeProcess:
    """Creates an iterative process using a given TFF `model_fn`.

    Args:
      model_fn: A no-arg function returning a `tff.learning.Model`.
      client_weight_fn: Optional function that takes the output of
        `model.report_local_outputs` and returns a tensor providing the weight
        in the federated average of model deltas. If not provided, the default
        is the total number of examples processed on device.
      dataset_preprocess_comp: Optional `tff.Computation` that sets up a data
        pipeline on the clients. The computation must take a squence of values
        and return a sequence of values, or in TFF type shorthand `(U* -> V*)`.
        If `None`, no dataset preprocessing is applied.

    Returns:
      A `tff.templates.IterativeProcess`.
    """

    return fed_avg_schedule.build_fed_avg_process(
        model_fn=model_fn,
        client_optimizer_fn=client_optimizer_fn,
        client_lr=client_lr_schedule,
        server_optimizer_fn=server_optimizer_fn,
        server_lr=server_lr_schedule,
        client_weight_fn=client_weight_fn,
        dataset_preprocess_comp=dataset_preprocess_comp)

  assign_weights_fn = fed_avg_schedule.ServerState.assign_weights_to_keras_model

  common_args = collections.OrderedDict([
      ('iterative_process_builder', iterative_process_builder),
      ('assign_weights_fn', assign_weights_fn),
      ('client_epochs_per_round', FLAGS.client_epochs_per_round),
      ('client_batch_size', FLAGS.client_batch_size),
      ('clients_per_round', FLAGS.clients_per_round),
      ('client_datasets_random_seed', FLAGS.client_datasets_random_seed)
  ])

  if FLAGS.task == 'cifar100':
    federated_cifar100.run_federated(
        **common_args, crop_size=FLAGS.cifar100_crop_size)

  elif FLAGS.task == 'emnist_cr':
    federated_emnist.run_federated(
        **common_args, emnist_model=FLAGS.emnist_cr_model)

  elif FLAGS.task == 'emnist_ae':
    federated_emnist_ae.run_federated(**common_args)

  elif FLAGS.task == 'shakespeare':
    federated_shakespeare.run_federated(
        **common_args, sequence_length=FLAGS.shakespeare_sequence_length)

  elif FLAGS.task == 'stackoverflow_nwp':
    so_nwp_flags = collections.OrderedDict()
    for flag_name in FLAGS:
      if flag_name.startswith('so_nwp_'):
        so_nwp_flags[flag_name[7:]] = FLAGS[flag_name].value
    federated_stackoverflow.run_federated(**common_args, **so_nwp_flags)

  elif FLAGS.task == 'stackoverflow_lr':
    so_lr_flags = collections.OrderedDict()
    for flag_name in FLAGS:
      if flag_name.startswith('so_lr_'):
        so_lr_flags[flag_name[6:]] = FLAGS[flag_name].value
    federated_stackoverflow_lr.run_federated(**common_args, **so_lr_flags)

  else:
    raise ValueError(
        '--task flag {} is not supported, must be one of {}.'.format(
            FLAGS.task, _SUPPORTED_TASKS))


if __name__ == '__main__':
  app.run(main)
