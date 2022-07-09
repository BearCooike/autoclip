from typing import Iterator, Dict, Union, Any, List

import torch
from copy import deepcopy
from itertools import chain
from collections import defaultdict
from autoclip.torch.utils import deep_tensor_move


class Clipper:
    """
    Modeled after torch.optim.Optimizer
    """

    def __init__(
        self,
        parameters: Iterator[torch.nn.parameter.Parameter],
        defaults: Dict[str, Any],
    ) -> None:
        self.parameter_groups: List[Dict[str, Any]] = []
        self.defaults = defaults
        self.state = defaultdict(dict)

        if not isinstance(parameters, Iterator):
            raise TypeError(
                "parameters argument given to the clipper should be "
                "an iterable of Tensors or dicts, but instead got "
                + torch.typename(parameters)
            )

        parameter_groups = list(parameters)
        if len(parameter_groups) == 0:
            raise ValueError(
                f"Clipper {type(self).__name__} got an empty parameter list"
            )
        if not isinstance(parameter_groups[0], dict):
            parameter_groups = [{"params": parameter_groups}]

        for parameter_group in parameter_groups:
            self.add_param_group(parameter_group=parameter_group)

    def step(self) -> None:
        raise NotImplementedError

    def state_dict(self) -> Dict[str, Any]:
        packed_parameter_groups = []
        for parameter_group in self.parameter_groups:
            packed_parameter_group = {
                key: value for key, value in parameter_group.items() if key != "params"
            }
            packed_parameter_group["params"] = [
                id(parameter) for parameter in parameter_group["params"]
            ]
            packed_parameter_groups.append(packed_parameter_group)

        packed_state = {
            (id(k) if isinstance(k, torch.Tensor) else k): v
            for k, v in self.state.items()
        }

        return {
            "state": packed_state,
            "param_groups": packed_parameter_groups,
        }

    def load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        loaded_state_dict = deepcopy(state_dict)
        local_groups, saved_groups = (
            self.parameter_groups,
            loaded_state_dict["param_groups"],
        )

        if len(local_groups) != len(saved_groups):
            raise ValueError(
                f"Loaded state dict has {len(saved_groups)} parameter "
                f"groups, Clipper {type(self).__name__} has "
                f"{len(local_groups)} parameter groups"
            )
        local_lens = (len(g["params"]) for g in local_groups)
        saved_lens = (len(g["params"]) for g in saved_groups)
        if any(p_len != s_len for p_len, s_len in zip(local_lens, saved_lens)):
            raise ValueError(
                "Loaded state dict contains a parameter group "
                "that doesn't match the size of Clipper "
                f"{type(self).__name__}'s group"
            )

        saved_id_to_parameter = {
            saved_id: parameter
            for saved_id, parameter in zip(
                chain.from_iterable([group["params"] for group in saved_groups]),
                chain.from_iterable([group["params"] for group in local_groups]),
            )
        }

        state = defaultdict(dict)
        for key, value in loaded_state_dict["state"].items():
            if key in saved_id_to_parameter:
                parameter = saved_id_to_parameter[key]
                state[parameter] = deep_tensor_move(value, parameter.device)
            else:
                state[key] = value

        new_parameter_groups = []
        for local_group, saved_group in zip(local_groups, saved_groups):
            saved_group["params"] = local_group["params"]
            new_parameter_groups.append(saved_group)

        self.state = state
        self.parameter_groups = new_parameter_groups

    def add_param_group(
        self,
        parameter_group: Dict[str, Union[torch.Tensor, List[torch.Tensor]]],
        **kwargs,
    ) -> None:
        """Add a param_group to the :class:`Optimizer` s `param_groups`.

        This can be useful when fine tuning a pre-trained network as frozen layers can be made
        trainable and added to the :class:`Optimizer` as training progresses.

        Args:
            param_group (dict): Specifies what Tensors should be optimized along with group
            specific optimization options.
        """
        assert isinstance(parameter_group, dict), "param_group must be a dict"

        parameters = parameter_group["params"]
        if isinstance(parameters, torch.Tensor):
            parameter_group["params"] = [parameters]
        elif isinstance(parameters, set):
            raise TypeError(
                "Clipping parameters must be ordered collections. "
                "The ordering of tensors in sets will change between runs."
                "Please use a list instead."
            )
        else:
            parameter_group["params"] = list(parameters)

        for parameter in parameter_group["params"]:
            if not isinstance(parameter, torch.Tensor):
                raise TypeError(
                    f"Clipper {type(self).__name__} can only clip Tensors, "
                    f"but one of the params is {torch.typename(parameter)}"
                )
            if not parameter.is_leaf:
                raise ValueError(
                    "Gradients to clip will only accumulate on leaf Tensors. "
                    f"{type(self).__name__} recieved non-leaf Tensor."
                )

        for name, default in self.defaults.items():
            parameter_group.setdefault(name, default)
        parameter_group.update(kwargs)

        parameters = parameter_group["params"]
        if len(parameters) != len(set(parameters)):
            raise ValueError(
                "Clipper contains a parameter group with duplicate parameters."
            )

        parameter_set = set()
        for group in self.parameter_groups:
            parameter_set.update(set(group["params"]))

        if not parameter_set.isdisjoint(set(parameter_group["params"])):
            raise ValueError(
                "Some clipping parameters appear in more than one parameter group"
            )

        self.parameter_groups.append(parameter_group)
