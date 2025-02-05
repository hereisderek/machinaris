#
# CLI interactions with the chia binary.
#

import datetime
import os
import psutil
import re
import signal
import shutil
import socket
import time
import traceback
import yaml

from flask import Flask, jsonify, abort, request, flash
from stat import S_ISREG, ST_CTIME, ST_MTIME, ST_MODE, ST_SIZE
from subprocess import Popen, TimeoutExpired, PIPE
from os import path

from app import app
from app.models import chia
from app.commands import global_config

CHIA_BINARY = '/chia-blockchain/venv/bin/chia'

RELOAD_MINIMUM_SECS = 30 # Don't query chia unless at least this long since last time.

# When reading tail of chia plots check output, limit to this many lines
MAX_LOG_LINES = 2000

last_farm_summary = None 
last_farm_summary_load_time = None 

def load_farm_summary():
    global last_farm_summary
    global last_farm_summary_load_time
    if last_farm_summary and last_farm_summary_load_time >= \
            (datetime.datetime.now() - datetime.timedelta(seconds=RELOAD_MINIMUM_SECS)):
        return last_farm_summary

    if global_config.plotting_only() or global_config.harvesting_only():  # Just get plot count and size
        last_farm_summary = chia.FarmSummary(farm_plots=load_plots_farming())
    else: # Load from chia farm summary
        proc = Popen("{0} farm summary".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
        try:
            outs, errs = proc.communicate(timeout=90)
        except TimeoutExpired:
            proc.kill()
            proc.communicate()
            abort(500, description="The timeout is expired!")
        if errs:
            app.logger.info("Failed to load chia farm summary at.")
            app.logger.info(traceback.format_exc())
        last_farm_summary = chia.FarmSummary(cli_stdout=outs.decode('utf-8').splitlines())
    last_farm_summary_load_time = datetime.datetime.now()
    return last_farm_summary

last_plots_farming = None 
last_plots_farming_load_time = None 

def load_plots_farming():
    global last_plots_farming
    global last_plots_farming_load_time
    if last_plots_farming and last_plots_farming_load_time >= \
            (datetime.datetime.now() - datetime.timedelta(seconds=RELOAD_MINIMUM_SECS)):
        return last_plots_farming
    all_entries = []
    for dir_path in os.environ['plots_dir'].split(':'):
        try:
            entries = (os.path.join(dir_path, file_name) for file_name in os.listdir(dir_path))
            entries = ((os.stat(path), path) for path in entries)
            entries = ((stat[ST_CTIME], stat[ST_SIZE], path) for stat, path in entries if S_ISREG(stat[ST_MODE]))
            all_entries.extend(entries)
        except:
            app.logger.info("Failed to list files at {0}".format(dir_path))
            app.logger.info(traceback.format_exc())
            flash('Unable to list plots at {0}.  Did you mount your plots directories?'.format(dir_path), 'danger')
    all_entries = sorted(all_entries, key=lambda entry: entry[0], reverse=True)
    last_plots_farming = chia.FarmPlots(all_entries)
    last_plots_farming_load_time = datetime.datetime.now()
    return last_plots_farming

def save_config(config):
    try:
        # Validate the YAML first
        yaml.safe_load(config)
        # Save a copy of the old config file
        src="/root/.chia/mainnet/config/config.yaml"
        dst="/root/.chia/mainnet/config/config."+time.strftime("%Y%m%d-%H%M%S")+".yaml"
        shutil.copy(src,dst)
        # Now save the new contents to main config file
        with open(src, 'w') as writer:
            writer.write(config)
    except Exception as ex:
        app.logger.info(traceback.format_exc())
        flash('Updated config.yaml failed validation! Fix and save or refresh page.', 'danger')
        flash(str(ex), 'warning')
    else:
        flash('Nice! config.yaml validated and saved successfully.', 'success')
        flash('NOTE: Currently requires restarting the container to pickup changes.', 'info')

last_wallet_show = None 
last_wallet_show_load_time = None 

def load_wallet_show():
    global last_wallet_show
    global last_wallet_show_load_time
    if last_wallet_show and last_wallet_show_load_time >= \
            (datetime.datetime.now() - datetime.timedelta(seconds=RELOAD_MINIMUM_SECS)):
        return last_wallet_show

    proc = Popen("echo 'S' | {0} wallet show".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        abort(500, description="The timeout is expired!")
    if errs:
        abort(500, description=errs.decode('utf-8'))
    last_wallet_show = chia.Wallet(outs.decode('utf-8').splitlines())
    last_wallet_show_load_time = datetime.datetime.now()
    return last_wallet_show

last_blockchain_show = None 
last_blockchain_show_load_time = None 

def load_blockchain_show():
    global last_blockchain_show
    global last_blockchain_show_load_time
    if last_blockchain_show and last_blockchain_show_load_time >= \
            (datetime.datetime.now() - datetime.timedelta(seconds=RELOAD_MINIMUM_SECS)):
        return last_blockchain_show

    proc = Popen("{0} show --state".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        abort(500, description="The timeout is expired!")
    if errs:
        abort(500, description=errs.decode('utf-8'))
    
    last_blockchain_show = chia.Blockchain(outs.decode('utf-8').splitlines())
    last_blockchain_show_load_time = datetime.datetime.now()
    return last_blockchain_show

last_connections_show = None 
last_connections_show_load_time = None 

def load_connections_show():
    global last_connections_show
    global last_connections_show_load_time
    if last_connections_show and last_connections_show_load_time >= \
            (datetime.datetime.now() - datetime.timedelta(seconds=RELOAD_MINIMUM_SECS)):
        return last_connections_show

    proc = Popen("{0} show --connections".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        abort(500, description="The timeout is expired!")
    if errs:
        abort(500, description=errs.decode('utf-8'))
    
    last_connections_show = chia.Connections(outs.decode('utf-8').splitlines())
    last_connections_show_load_time = datetime.datetime.now()
    return last_connections_show

def add_connection(connection):
    try:
        hostname,port = connection.split(':')
        if socket.gethostbyname(hostname) == hostname:
            app.logger.info('{} is a valid IP address'.format(hostname))
        elif socket.gethostbyname(hostname) != hostname:
            app.logger.info('{} is a valid hostname'.format(hostname))
        proc = Popen("{0} show --add-connection {1}".format(CHIA_BINARY, connection), stdout=PIPE, stderr=PIPE, shell=True)
        try:
            outs, errs = proc.communicate(timeout=90)
        except TimeoutExpired:
            proc.kill()
            proc.communicate()
            abort(500, description="The timeout is expired!")
        if errs:
            abort(500, description=errs.decode('utf-8'))
    except Exception as ex:
        app.logger.info(traceback.format_exc())
        flash('Invalid connection "{0}" provided.  Must be HOST:PORT.'.format(connection), 'danger')
        flash(str(ex), 'warning')
    else:
        app.logger.info("{0}".format(outs.decode('utf-8')))
        flash('Nice! Connection added to Chia and sync engaging!', 'success')

last_keys_show = None 
last_keys_show_load_time = None 

def load_keys_show():
    global last_keys_show
    global last_keys_show_load_time
    if last_keys_show and last_keys_show_load_time >= \
            (datetime.datetime.now() - datetime.timedelta(seconds=RELOAD_MINIMUM_SECS)):
        return last_keys_show

    proc = Popen("{0} keys show".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        abort(500, description="The timeout is expired!")
    if errs:
        abort(500, description=errs.decode('utf-8'))
    
    last_keys_show = chia.Keys(outs.decode('utf-8').splitlines())
    last_keys_show_load_time = datetime.datetime.now()
    return last_keys_show 

def generate_key(key_path):
    if os.path.exists(key_path) and os.stat(key_path).st_size > 0:
        app.logger.info('Skipping key generation as file exists and is NOT empty! {0}'.format(key_path))
        flash('Skipping key generation as file exists and is NOT empty!', 'danger')
        flash('key_path={0}'.format(key_path), 'warning')
        return False
    proc = Popen("{0} keys generate".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        app.logger.info(traceback.format_exc())
        flash('Timed out while generating keys!', 'danger')
        flash(str(ex), 'warning')
        return False
    if errs:
        app.logger.info("{0}".format(errs.decode('utf-8')))
        flash('Unable to generate keys!', 'danger')
        return False
    proc = Popen("{0} keys show --show-mnemonic-seed | tail -n 1 > {1}".format(CHIA_BINARY, key_path), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        app.logger.info(traceback.format_exc())
        flash('Timed out while generating keys!', 'danger')
        flash(str(ex), 'warning')
        return False
    if errs:
        app.logger.info("{0}".format(errs.decode('utf-8')))
        flash('Unable to save mnemonic to {0}'.format(key_path), 'danger')
        return False
    else:
        app.logger.info("Store mnemonic output: {0}".format(outs.decode('utf-8')))
        try:
            mnemonic_words = open(key_path,'r').read().split()
            if len(mnemonic_words) != 24:
                flash('{0} does not contain a 24-word mnemonic!'.format(key_path), 'danger')
                return False
        except:
                flash('{0} was unreadable or not found.'.format(key_path), 'danger')
                return False
        flash('Welcome! A new key has been generated at {0}. Keep it secret! Keep it safe!'.format(key_path), 'success')
        flash('{0}'.format(" ".join(mnemonic_words)), 'info')
    proc = Popen("{0} start farmer".format(CHIA_BINARY), stdout=PIPE, stderr=PIPE, shell=True)
    try:
        outs, errs = proc.communicate(timeout=90)
    except TimeoutExpired:
        proc.kill()
        proc.communicate()
        app.logger.info(traceback.format_exc())
        flash('Timed out while starting farmer! Try restarting the Machinaris container.', 'danger')
        flash(str(ex), 'warning')
        return False
    if errs:
        app.logger.info("{0}".format(errs.decode('utf-8')))
        flash('Unable to start farmer. Try restarting the Machinaris container.'.format(key_path), 'danger')
        flash(str(ex), 'warning')
        return False
    return True

def remove_connection(node_id, ip):
    try:
        proc = Popen("{0} show --remove-connection {1}".format(CHIA_BINARY, node_id), stdout=PIPE, stderr=PIPE, shell=True)
        try:
            outs, errs = proc.communicate(timeout=90)
        except TimeoutExpired:
            proc.kill()
            proc.communicate()
            app.logger.info("The timeout is expired!")
            return False
        if errs:
            app.logger.info(errs.decode('utf-8'))
            return False
        if outs:
            app.logger.info(outs.decode('utf-8'))
    except Exception as ex:
        app.logger.info(traceback.format_exc())
    app.logger.info("Successfully removed connection to {0}".format(ip))
    return True

def compare_plot_counts(global_config, farming, plots):
    if not global_config['plotting_only']:
        try:
            if int(farming.plot_count) < len(plots.rows):
                flash("Warning! Chia is farming {0} plots, but Machinaris found {1} *.plot files on disk. See the <a href='https://github.com/guydavis/machinaris/wiki/FAQ#warning-chia-is-farming-x-plots-but-machinaris-found-y-plot-files-on-disk' target='_blank'>FAQ</a>.".format(farming.plot_count, len(plots.rows), 'warning'))
        except:
            app.logger.info("Compare plots failed to check matching plot counts.")
            app.logger.info(traceback.format_exc())

def is_plots_check_running():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        if proc.info['name'] == 'chia' and 'plots' in proc.info['cmdline'] and 'check' in proc.info['cmdline']:
            return proc.info['pid']
    return None

def check_plots(first_load):
    output_file = '/root/.chia/mainnet/log/plots_check.log'
    if not is_plots_check_running() and first_load == "true":
        try:
            log_fd = os.open(output_file, os.O_RDWR | os.O_CREAT)
            log_fo = os.fdopen(log_fd, "a+")
            proc = Popen("{0} plots check".format(CHIA_BINARY), shell=True, 
                universal_newlines=True, stdout=log_fo, stderr=log_fo)
        except:
            app.logger.info(traceback.format_exc())
            return 'Failed to start plots check job!'
        else:
            return "Starting chia plots check at " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    else:
        class_escape = re.compile(r' chia.plotting.(\w+)(\s+): ')
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        proc = Popen(['tail', '-n', str(MAX_LOG_LINES), output_file], stdout=PIPE)
        return  class_escape.sub('', ansi_escape.sub('', proc.stdout.read().decode("utf-8")))
