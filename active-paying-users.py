#!/usr/bin/env python3
import os
import json
import argparse
import sys

from util import util
from s3 import s3
from es import es
from eslog import eslog
# run afrer payment-account.py
S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX")
PLAYER_LOGIN_EVENT = os.getenv("PLAYER_LOGIN_EVENT")

ES_PAYING_USERS_INDEX = os.getenv(
    "ES_PAYING_USERS_INDEX", "paying-users")
ES_ACTIVE_PAYING_USERS_INDEX = os.getenv(
    "ES_ACTIVE_PAYING_USERS_INDEX", "active-paying-users")

# channel diff in CHANNELS
# upper or lower not in CHANNELS
CHANNELS = {
    "GOOGLE_PLAY": "google_store"
}

bucket = None
logger = eslog.get_logger(ES_ACTIVE_PAYING_USERS_INDEX)


def valid_params():
    params_errors = []
    if util.is_empty(ES_PAYING_USERS_INDEX):
        params_errors.append("ES_PAYING_USERS_INDEX")

    if util.is_empty(S3_KEY_PREFIX):
        params_errors.append("S3_KEY_PREFIX")

    if util.is_empty(ES_ACTIVE_PAYING_USERS_INDEX):
        params_errors.append("ES_ACTIVE_PAYING_USERS_INDEX")

    if util.is_empty(PLAYER_LOGIN_EVENT):
        params_errors.append("PLAYER_LOGIN_EVENT")

    if len(params_errors) != 0:
        logger.error(f'Params error. {params_errors} is empty')
        raise RuntimeError()


def arg_parse(*args, **kwargs):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d", "--day",
        nargs="?",
        const=1,
        type=util.valid_date,
        default=util.get_yesterday(),
        help="Date. The default date is yesterday. The format is YYYY-MM-DD"
    )
    args = parser.parse_args()
    process(args.day)


def process(time_str):
    valid_params()
    global bucket
    bucket = s3.init_bucket_from_env()
    login_players, platform_and_channels = compute(time_str)
    output_to_es(time_str, login_players, platform_and_channels)
    logger.info("Process end.")

# ==========================for compute retention==============================


def compute(time_str):
    login_counts = {}
    paying_users = get_paying_users()
    if len(paying_users) == 0:
        return {}, {}
    login_players, platform_and_channels = get_login_players(time_str)
    for key, ids in login_players.items():
        intersection_set = paying_users.intersection(ids)
        login_size = len(intersection_set)
        login_counts[key] = login_size
    return login_counts, platform_and_channels


def get_paying_users():
    ret = set()
    logger.info("Get pay player from es")
    logs = es.query_match_all(ES_PAYING_USERS_INDEX, es.get_match_all_dsl())
    logger.info(f"Pay player size is {len(logs)}")
    for log in logs:
        ret.add(log["_id"])
    return ret


def get_login_players(time_str):
    players = {}
    platform_and_channels = {}
    logger.info("Get login player from s3")
    days = util.get_days_with_today(time_str)
    logs, exist = util.get_logs(
        bucket, PLAYER_LOGIN_EVENT, S3_KEY_PREFIX, days)
    if not exist:
        return players, platform_and_channels
    logger.info(f"Login player size is {len(logs)}")
    for log in logs:
        add_player_by_platform_and_channel(players, platform_and_channels, log)
    return players, platform_and_channels


def add_player_by_platform_and_channel(players, platform_and_channels, log):
    channel = log["channel"]
    key = log["platform"].lower() + "_" + channel.lower()
    if channel in CHANNELS:
        channel = CHANNELS[channel]
        key = log["platform"].lower() + "_" + channel
    id = util.get_paying_users_index_id(
            log["player_id"], log["platform"], channel)
    if key not in players:
        platform_and_channels[key] = log
        ids = set()
        ids.add(id)
        players[key] = ids
    else:
        players[key].add(id)


# ==========================for output to es=============================


def output_to_es(time_str, login_counts, platform_and_channels):
    if len(login_counts) == 0:
        return
    for key, count in login_counts.items():
        path = ES_ACTIVE_PAYING_USERS_INDEX + "/_doc/" + key
        platform_and_channel = platform_and_channels[key]
        platform = platform_and_channel["platform"]
        channel = platform_and_channel["channel"]
        data = es_get_doc(time_str, count, platform, channel)
        es.add_doc(path, data)
        logger.info(f"Output to es. path: {path}. data is {data}")


def es_get_doc(time_str, login_count, platform, channel):
    timestamp = util.get_timestamp(time_str)
    channel = channel.lower()
    if channel in CHANNELS:
        channel = CHANNELS[channel]
    data = {
        "@timestamp": timestamp,
        "count": login_count,
        "platform": platform.lower(),
        "channel": channel
    }
    return json.dumps(data)


if __name__ == '__main__':
    try:
        sys.exit(arg_parse(*sys.argv))
    except KeyboardInterrupt:
        logger.info("CTL-C Pressed.")
        exit("CTL-C Pressed.")
    except Exception as e:
        logger.exception(e)
        exit("Exception")
