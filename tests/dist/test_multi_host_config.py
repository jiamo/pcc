from __future__ import annotations

import json

import pytest

from pcc.dist.multi_host import MultiHostConfigError, load_multi_host_config


def _config_file(tmp_path):
    path = tmp_path / "cluster.json"
    path.write_text(
        json.dumps(
            {
                "cluster_id": "two-mac-test",
                "nodes": [
                    {"rank": 0, "host": "mac-a.example:7210"},
                    {"rank": 1, "host": "mac-b.example:7210"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_explicit_two_host_config_carries_rank_psk_and_bounded_timeouts(tmp_path):
    path = _config_file(tmp_path)
    config = load_multi_host_config(
        {
            "PCC_DIST_CLUSTER_CONFIG": str(path),
            "PCC_DIST_RANK": "1",
            "PCC_DIST_ADMISSION_KEY_HEX": bytes(range(32)).hex(),
            "PCC_DIST_CONNECT_TIMEOUT_S": "12",
            "PCC_DIST_IO_TIMEOUT_S": "4",
        }
    )
    assert config.rank == 1
    assert config.manifest.world_size == 2
    assert [node.host for node in config.manifest.nodes] == [
        "mac-a.example:7210",
        "mac-b.example:7210",
    ]
    assert config.owner_options() == {
        "allow_remote": True,
        "admission_key": bytes(range(32)),
        "connect_timeout_s": 12.0,
        "io_timeout_s": 4.0,
    }


@pytest.mark.parametrize(
    ("env_update", "message"),
    [
        ({"PCC_DIST_RANK": ""}, "PCC_DIST_RANK is required"),
        ({"PCC_DIST_RANK": "2"}, "outside world_size"),
        ({"PCC_DIST_ADMISSION_KEY_HEX": "aa"}, "at least 256 bits"),
        ({"PCC_DIST_CONNECT_TIMEOUT_S": "0"}, "must be in"),
    ],
)
def test_two_host_config_fails_closed(tmp_path, env_update, message):
    path = _config_file(tmp_path)
    env = {
        "PCC_DIST_CLUSTER_CONFIG": str(path),
        "PCC_DIST_RANK": "0",
        "PCC_DIST_ADMISSION_KEY_HEX": (b"k" * 32).hex(),
    }
    env.update(env_update)
    with pytest.raises(MultiHostConfigError, match=message):
        load_multi_host_config(env)


def test_strict_config_rejects_loopback_or_same_host(tmp_path):
    path = tmp_path / "cluster.json"
    path.write_text(
        json.dumps(
            {
                "cluster_id": "not-two-macs",
                "nodes": [
                    {"rank": 0, "host": "127.0.0.1:7210"},
                    {"rank": 1, "host": "127.0.0.1:7211"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(MultiHostConfigError, match="non-loopback"):
        load_multi_host_config(
            {
                "PCC_DIST_CLUSTER_CONFIG": str(path),
                "PCC_DIST_RANK": "0",
                "PCC_DIST_ADMISSION_KEY_HEX": (b"k" * 32).hex(),
            }
        )
