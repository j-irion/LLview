#!/usr/bin/env python3
# Copyright (c) 2023 Forschungszentrum Juelich GmbH.
# This file is part of LLview.
#
# This is an open source software distributed under the GPLv3 license. More information see the LICENSE file at the top level.
#
# Contributions must follow the Contributor License Agreement. More information see the CONTRIBUTING.md file at the top level.
#
# Contributors:
#    Filipe Guimarães (Forschungszentrum Juelich GmbH)
#    Yannik Müller (Forschungszentrum Juelich GmbH)

import argparse
import logging
import time
from datetime import datetime
import re
import os
import sys
import math
import csv
import collections
from copy import deepcopy
from subprocess import check_output
from kubernetes import client, config


def expand_NodeList(nodelist: str) -> str:
    """
    Split node list by commas only between groups of nodes, not within groups
    Returns all complete node names separated by a single space
    Regex notes:
      [^\[]+ - matches anything (at lest one character) but the '[' literal
      (?:\[[\d,-]*\])? - matches zero or one (optional) node groupings like '[1,3,5-8,10]'
      General Documentation https://docs.python.org/3/library/re.html#regular-expression-syntax
    """
    expandedlist = ""
    for nodelist in re.findall("([^\[]+(?:\[[\d,-]*\])?),?", nodelist):
        match = re.findall("(.+?)\[(.*?)\]|(.+)", nodelist)[0]
        if match[2] == nodelist:
            # single node
            expandedlist += f"{nodelist} "
            continue
        # multiple nodes in node list as in "node[a,b,m-n,x-y]"
        for node in match[1].split(","):
            # splitting eventual consecutive nodes with '-'
            list = node.split("-", 1)
            if len(list) == 1:
                # single node
                expandedlist += f"{match[0]}{list[0]} "
            else:
                # multi-node separated by '-'
                for i in range(int(list[0]), int(list[1]) + 1):
                    expandedlist += f"{match[0]}{i:0{len(list[0])}} "
    return expandedlist.rstrip()


def add_CPUs_to_nodelist(nodelist: str, numcpus: int) -> str:
    expandedlist = ""
    for node in nodelist.split(" "):
        expandedlist += f"({node},{numcpus})"
    return expandedlist


def remove_duplicate(id: str) -> str:
    """
    Remove duplicate values from id
    """
    match = re.match("(\d+)-(\d+)$", id)
    return match.group(1) if match else id


def remove_key(value: str) -> str:
    """
    Remove keyword and '=' sign
    """
    newvalue = value.split("=", 1)
    return newvalue[1] if len(newvalue) == 2 else value


def remove_id_num(id: str) -> str:
    """
    Remove id number (inside parenthesis)
    """
    return re.match("(.+)\(\d+\)$", id).group(1)


def to_seconds(time: str) -> int:
    """
    Transform different time formats to number of seconds (integer)
    """
    ret = time
    patint = "([\+\-]?[\d]+)"
    if match := re.match(f"\({patint} seconds\)", time):
        ret = int(match.group(1))
    elif match := re.match(f"{patint} minutes", time):
        ret = int(match.group(1)) * 60
    elif match := re.match(f"^{patint}[:]{patint}[:]{patint}$", time):
        ret = (
            int(match.group(1)) * 60 * 60
            + int(match.group(2)) * 60
            + int(match.group(3))
        )
    elif match := re.match(f"^{patint}[-]{patint}[:]{patint}[:]{patint}$", time):
        ret = (
            int(match.group(1)) * 24 * 60 * 60
            + int(match.group(2)) * 60 * 60
            + int(match.group(3)) * 60
            + int(match.group(4))
        )
    return ret


def to_hours(time: str) -> int:
    if not time:
        return time
    comp = [float(_) for _ in re.split("[-:]", time)]
    ret = (
        comp[-4] * 24
        if len(comp) == 4
        else 0 + comp[-3] + comp[-2] / 60 + comp[-1] / 60 / 60
    )
    return ret


def get_state(job_state: str, reason: str) -> tuple[str, str]:
    """
    Define the jobstate
    """
    # status = "UNDETERMINED"
    # detailed_status = "QUEUED_ACTIVE"
    # if job_state == "PENDING" or job_state == "SUSPENDED":
    #     status = "SUBMITTED"
    #     if reason == "JobHeldUser":
    #         detailed_status = "USER_ON_HOLD"
    #     elif reason == "JobHeldAdmin":
    #         detailed_status = "SYSTEM_ON_HOLD"
    # elif status == "CONFIGURING":
    #     status = "SUBMITTED"
    # elif job_state == "RUNNING":
    #     status = "RUNNING"
    # elif job_state == "COMPLETED" or job_state == "COMPLETING":
    #     status = "COMPLETED"
    #     detailed_status = "JOB_OUTERR_READY"
    # elif job_state == "CANCELLED":
    #     status = "COMPLETED"
    #     detailed_status = "CANCELLED"
    # elif job_state == "FAILED" or job_state == "NODE_FAIL" or job_state == "TIMEOUT":
    #     status = "COMPLETED"
    #     detailed_status = "FAILED"
    return job_state, reason


def modify_date(date: str) -> str:
    return date.replace("T", " ") if date != "Unknown" else ""


def modify_state(state: str) -> str:
    """
    Adapt the 'state' value
    """
    ret = state
    # Nodes
    if state == "NoResp":
        ret = "Down"
    elif state == "ALLOC":
        ret = "Running"
    elif state == "ALLOCATED":
        ret = "Running"
    elif state == "DOWN":
        ret = "Down"
    elif state == "DRAIN":
        ret = "Drained"
    elif state == "FAIL":
        ret = "Down"
    elif state == "FAILING":
        ret = "Down"
    elif state == "IDLE":
        ret = "Idle"
    elif state == "MIXED":
        ret = "Running"
    elif state == "MAINT":
        ret = "Maint"
    elif state == "POWER_DOWN":
        ret = "Down"
    elif state == "POWER_UP":
        ret = "Down"
    elif state == "RESUME":
        ret = "Down"
    elif state == "DOWN+DRAIN":
        ret = "Down"
    elif state == "DOWN*+DRAIN":
        ret = "Down"
    elif state == "MAINT+DRAIN":
        ret = "Down"
    elif state == "MAINT*+DRAIN":
        ret = "Maint"
    elif state == "UNKNOWN+MAINTENANCE":
        ret = "Maint"
    elif state == "IDLE+DRAIN":
        ret = "Drained"
    elif state == "RESERVED+DRAIN":
        ret = "Drained"
    elif state == "IDLE+COMPLETING":
        ret = "Idle"

    # Jobs
    elif state == "CANCELLED":
        ret = "Cancelled"
    elif state == "COMPLETED":
        ret = "Completed"
    elif state == "CONFIGURING":
        ret = "Pending"
    elif state == "COMPLETING":
        ret = "Completed"
    elif state == "FAILED":
        ret = "Failed"
    elif state == "NODE_FAIL":
        ret = "Failed"
    elif state == "PENDING":
        ret = "Pending"
    elif state == "RUNNING":
        ret = "Running"
    elif state == "SUSPENDED":
        ret = "Suspended"
    elif state == "TIMEOUT":
        ret = "Failed"

    return ret


def modify_load(load: str) -> str:
    """
    Fix load value when == N/A
    """
    return "-1" if load == "N/A" else load


def id_to_username(state: str) -> str:
    """
    Convert user id to username (must be run in the computer where the information is obtainable by `id <uid>`)
    """
    log = logging.getLogger("logger")

    ret = state
    # Getting the username for "CANCELLED by id" messages
    if match := re.match("^CANCELLED by ([\w]+)$", state):
        id = match.group(1)
        rawoutput = check_output(f"id {id}", shell=True, text=True)
        if match := re.match(f"^uid={id}\((.+?)\).*$", rawoutput):
            ret = f"CANCELLED by {match.group(1)}"
        else:
            log.error(f"Error getting username of uid {id}\n")
    return ret


def sysinfo(options: dict, slurm_info) -> dict:
    """
    Specific function to add extra items to sysinfo
    """

    log = logging.getLogger("logger")

    # Getting basic information from the system (currently only 'cluster' type)
    log.info("Collecting system information...\n")

    import platform

    sysextra = {}
    sysinfoid = "cluster"
    sysextra[sysinfoid] = {}
    sysextra[sysinfoid]["hostname"] = platform.node()
    sysextra[sysinfoid]["system_time"] = time.strftime("%m/%d/%y-%H:%M:%S")
    sysextra[sysinfoid]["type"] = "Cluster"
    sysextra[sysinfoid]["__type"] = "system"
    sysextra[sysinfoid]["__prefix"] = "sys"

    # If motd file is given, read it and get the data
    if "motd" in options:
        try:
            with open(options["motd"], "r") as file:
                sysextra[sysinfoid]["motd"] = ""
                for line in file:
                    line = line.strip("\n")
                    # Skip initial lines starting with '*'
                    if re.match("^\*+$", line) or re.match("^\*\*", line):
                        continue
                    line = re.sub("^\*\s+|\s+\*$", "", line)
                    line = line.replace(
                        '"', "&quot;"
                    )  # Escaping double quotes on the xml
                    sysextra[sysinfoid]["motd"] += line + "\\n"
        except FileNotFoundError:
            log.error(f"motd file {options['motd']} does not exist! Skipping it...\n")
    return sysextra


def nodeinfo(options: dict, nodes_info) -> dict:
    """
    Specific function to add extra items to nodeinfo
    """
    log = logging.getLogger("logger")

    nodeextra = {}

    # Updating the nodes dictionary by adding or removing keys
    for nodename, nodeinfo in nodes_info.items():
        # Adding gpus information for GPU nodes (which include 'Gres' key)
        if ("Gres" in nodeinfo) and (match := re.search("gpu:(\d)", nodeinfo["Gres"])):
            nodeinfo["gpus"] = match.group(1)

    # Gathering information about the partitions
    partitions = SlurmInfo()
    # partitions.parse("scontrol show part --detail --all")
    partitions.to_LML("./partitions_LML.xml", "part")

    # Adding information about the different classes/partitions in each node
    nodes_info_dict = nodes_info._dict
    for partname, partition in partitions.items():
        if (
            ("Nodes" in partition)
            and ("TotalNodes" in partition)
            and ("TotalCPUs" in partition)
        ):
            if partition["TotalNodes"] == 0:
                continue
        for node in expand_NodeList(partition["Nodes"]).split(" "):
            if node in nodes_info_dict:
                nodeextra.setdefault(node, {})
                nodeextra[node].setdefault("classes", "")
                # Getting total number of CPUs/Threads in each partition, in this priority
                # From nodes_info,'CPUTot' -> nodes_info,'ThreadsPerCore'*'CoresPerSocket'*'Sockets' -> from partition,'TotalCPUs'/'TotalNodes'
                if "CPUTot" in nodes_info_dict[node]:
                    nodeextra[node][
                        "classes"
                    ] += f"[{partname}:{nodes_info_dict[node]['CPUTot']}]"
                elif (
                    "ThreadsPerCore" in nodes_info_dict[node]
                    and "CoresPerSocket" in nodes_info_dict[node]
                    and "Sockets" in nodes_info_dict[node]
                ):
                    nodeextra[node][
                        "classes"
                    ] += f"[{partname}:{nodes_info_dict[node]['ThreadsPerCore'] * nodes_info_dict[node]['CoresPerSocket'] * nodes_info_dict[node]['Sockets']}]"
                else:
                    nodeextra[node][
                        "classes"
                    ] += f"[{partname}:{partition['TotalCPUs'] / partition['TotalNodes']}]"
            # else:
            #   log.debug(f"Unknown node {node} in partition {partname}!\n")

    return nodeextra


def jobinfo(options: dict, jobs_info) -> dict:
    """
    Specific function to add extra items to jobinfo
    """
    log = logging.getLogger("logger")

    jobextra = {}

    # Updating the jobs dictionary by adding or removing keys
    for jobname, jobinfo in jobs_info.items():
        # Adding status and detailedstatus to jobs
        jobinfo["status"], jobinfo["detailedstatus"] = get_state(
            jobinfo["JobState"] if "JobState" in jobinfo else "",
            jobinfo["Reason"] if "Reason" in jobinfo else "",
        )

        # jobinfo['totaltasks'] = jobinfo['NumCPUs'] 'totaltasks' $jobs{"$jobid"}{NumCPUs} if (!exists($jobs{"$jobid"}{totaltasks}))

    # Gathering information about the job steps
    # steps = SlurmInfo()
    # steps.parse('scontrol show steps')
    # # steps.to_LML('./steps_LML.xml',prefix='st',stype='steps')
    # for stepname,step in steps.items():
    #   match = re.match('((\w+)\.\w+)',stepname)
    #   jobid = re.match('((\w+)\.\w+)',stepname).group(2)

    return jobextra


def stepinfo(options: dict, steps_info) -> dict:
    """
    Specific function to add extra items to jobinfo
    """
    stepsextra = {}
    # Updating the jobs dictionary by adding or removing keys
    for stepname, stepinfo in steps_info.items():
        # Obtaining 'jobid' and 'step' from stepname and adding to steps_info
        match = re.match("^([\d\_\+]+)\.?(.*)$", stepname)
        stepsextra.setdefault(stepname, {})
        stepsextra[stepname]["jobid"] = match.group(1)
        stepsextra[stepname]["step"] = match.group(2) if match.group(2) else "job"
        # Obtaining 'rc' and 'signr' from ExitCode and adding to steps_info
        match = re.match("^(\d*):?(\d*)$", stepinfo["ExitCode"])
        stepsextra[stepname]["rc"] = match.group(1) if match.group(1) else "-"
        stepsextra[stepname]["signr"] = match.group(2) if match.group(2) else "-"
    return stepsextra


def parse_resource_value(val):
    """
    Parses a resource value string and converts it to a numerical value.
    For CPU resources, the value is in cores (where 'm' indicates millicores).
    For memory resources, the value is converted to bytes.
    """
    if val.endswith("m"):
        # Convert millicores to cores (no direct relation to bytes)
        return float(val[:-1]) / 1000
    elif val.endswith("Mi"):
        return float(val[:-2]) * (2**20)  # Convert Mebibytes to bytes
    elif val.endswith("Gi"):
        return float(val[:-2]) * (2**30)  # Convert Gibibytes to bytes
    elif val.endswith("Ki"):
        return float(val[:-2]) * (2**10)  # Convert Kibibytes to bytes
    # If no unit is specified or unrecognized unit, attempt to treat as raw number (bytes for memory, cores for CPU)
    return float(val)


def parse_memory_to_bytes(memory_str):
    """
    Parses a Kubernetes memory string (e.g., "2048Ki", "2Gi") into bytes.
    """
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
    num = float("".join(filter(str.isdigit, memory_str)))
    unit = "".join(filter(str.isalpha, memory_str))
    return int(num * units.get(unit, 1))  # Default to bytes if no unit


class SlurmInfo:
    """
    Class that stores and processes information from Slurm output
    """

    def __init__(self):
        self._dict = {}
        self._raw = {}
        self.log = logging.getLogger("logger")

    def __add__(self, other):
        first = self
        second = other
        first._raw |= second._raw
        first.add(second._dict)
        return first

    def __iter__(self):
        return (t for t in self._dict.keys())

    def __len__(self):
        return len(self._dict)

    def items(self):
        return self._dict.items()

    def __delitem__(self, key):
        del self._dict[key]

    def add(self, to_add: dict, add_to=None):
        """
        (Deep) Merge dictionary 'to_add' into internal 'self._dict'
        """
        # self._dict |= dict
        if not add_to:
            add_to = self._dict
        for bk, bv in to_add.items():
            av = add_to.get(bk)
            if isinstance(av, dict) and isinstance(bv, dict):
                self.add(bv, add_to=av)
            else:
                add_to[bk] = deepcopy(bv)
        return

    def empty(self):
        """
        Check if internal dict is empty: Boolean function that returns True if _dict is empty
        """
        return not bool(self._dict)

    def parse(self, methods, prefix="", stype=""):
        """
        This function parses the output of Slurm commands
        and returns them in a dictionary
        """

        self.log.debug(f"Methods to be parsed: {methods}\n")
        # get and run methods
        for method_name in methods:
            method = getattr(self, method_name, None)
            if method:
                if method_name in ["get_node_info", "get_job_info"]:
                    method(prefix, stype)
                method()
            else:
                self.log.error(
                    f"Error executing method! Method '{method_name}' not found."
                )

    def add_value(self, key, value, dict):
        """
        Function to add (key,value) pair to dict. It is separate to be easier to adapt
        (e.g., to not include empty keys)
        """
        dict[key] = value if value != "(null)" else ""
        return

    def apply_pattern(self, exclude="", include=""):
        """
        Loops over all units in self._dict to:
        - remove items that match 'exclude'
        - keep only items that match 'include'
        """
        to_remove = []
        for unitname, unit in self._dict.items():
            if (
                exclude
                and self.check_unit(unitname, unit, exclude, text="excluded") == True
            ):
                to_remove.append(unitname)
            if (
                include
                and self.check_unit(unitname, unit, include, text="included") == False
            ):
                to_remove.append(unitname)
        for unitname in to_remove:
            del self._dict[unitname]
        return

    def check_unit(self, unitname, unit, pattern, text="included/excluded"):
        """
        Check 'current_unit' name with rules for exclusion or inclusion. (exclusion is applied first)
        Returns True if unit is to be skipped
        """
        if isinstance(pattern, str):  # If rule is a simple string
            if re.match(pattern, unitname):
                self.log.debug(f"Unit {unitname} is {text} due to {pattern} rule\n")
                return True
        elif isinstance(pattern, list):  # If list of rules
            for pat in pattern:  # loop over list - that can be strings or dictionaries
                if isinstance(pat, str):  # If item in list is a simple string
                    if re.match(pat, unitname):
                        self.log.debug(
                            f"Unit {unitname} is {text} due to {pat} rule in list\n"
                        )
                        return True
                elif isinstance(pat, dict):  # If item in list is a dictionary
                    for key, value in pat.items():
                        if isinstance(
                            value, str
                        ):  # if dictionary value is a simple string
                            if (key in unit) and re.match(value, unit[key]):
                                self.log.debug(
                                    f"Unit {unitname} is {text} due to {value} rule in {key} key of list\n"
                                )
                                return True
                        elif isinstance(value, list):  # if dictionary value is a list
                            for v in value:
                                if (key in unit) and re.match(
                                    v, unit[key]
                                ):  # At this point, v in list can only be a string
                                    self.log.debug(
                                        f"Unit {unitname} is {text} due to {v} rule in list of {key} key of list\n"
                                    )
                                    return True
        elif isinstance(pattern, dict):  # If dictionary with rules
            for key, value in pattern.items():
                if isinstance(value, str):  # if dictionary value is a simple string
                    if (key in unit) and re.match(value, unit[key]):
                        self.log.debug(
                            f"Unit {unitname} is {text} due to {value} rule in {key} key\n"
                        )
                        return True
                elif isinstance(value, list):  # if dictionary value is a list
                    for v in value:
                        if (key in unit) and re.match(
                            v, unit[key]
                        ):  # At this point, v in list can only be a string
                            self.log.debug(
                                f"Unit {unitname} is {text} due to {v} rule in list of {key} key\n"
                            )
                            return True
        return False

    def map(self, mapping_dict):
        """
        Map the dictionary using (key,value) pair in mapping_dict
        (Keys that are not present are removed)
        """
        new_dict = {}
        skip_keys = set()
        for unit, item in self._dict.items():
            new_dict[unit] = {}
            for key, map in mapping_dict.items():
                # Checking if key to be modified is in object
                if key not in item:
                    skip_keys.add(key)
                    continue
                new_dict[unit][map] = item[key]
            # Copying also internal keys that are used in the LML
            if "__type" in item:
                new_dict[unit]["__type"] = item["__type"]
            if "__id" in item:
                new_dict[unit]["__id"] = item["__id"]
            if "__prefix" in item:
                new_dict[unit]["__prefix"] = item["__prefix"]
        if skip_keys:
            self.log.warning(
                f"Skipped mapping keys (at least on one node): {', '.join(skip_keys)}\n"
            )
        self._dict = new_dict
        return

    def modify(self, modify_dict):
        """
        Modify the dictionary using functions given in modify_dict
        """
        skipped_keys = set()
        for item in self._dict.values():
            for key, modify in modify_dict.items():
                # Checking if key to be modified is in object
                if key not in item:
                    skipped_keys.add(key)
                    continue
                if isinstance(modify, str):
                    for funcname in [_.strip() for _ in modify.split(",")]:
                        try:
                            func = globals()[funcname]
                            item[key] = func(item[key])
                        except KeyError:
                            self.log.error(
                                f"Function {funcname} is not defined. Skipping it and keeping value {item[key]}\n"
                            )
                elif isinstance(modify, list):
                    for funcname in modify:
                        try:
                            func = globals()[funcname]
                            item[key] = func(item[key])
                        except KeyError:
                            self.log.error(
                                f"Function {funcname} is not defined. Skipping it and keeping value {item[key]}\n"
                            )
        if skipped_keys:
            self.log.warning(
                f"Skipped modifying keys (at least on one node): {', '.join(skipped_keys)}\n"
            )
        return

    def to_LML(self, filename, prefix="", stype=""):
        """
        Create LML output file 'filename' using
        information of self._dict
        """
        self.log.info(f"Writing LML data to {filename}... \n")
        # Opening LML file
        with open(filename, "w") as file:
            # Writing initial XML preamble
            file.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            file.write(
                '<lml:lgui xmlns:lml="http://eclipse.org/ptp/lml" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
            )
            file.write(
                '    xsi:schemaLocation="http://eclipse.org/ptp/lml http://eclipse.org/ptp/schemas/v1.1/lgui.xsd"\n'
            )
            file.write('    version="1.1">\n')

            # Creating first list of objects
            file.write("<objects>\n")
            digits = int(math.log10(len(self._dict))) + 1 if len(self._dict) > 0 else 1
            i = 0
            for key, item in self._dict.items():
                if "__id" not in item:
                    item["__id"] = (
                        f'{prefix if prefix else item["__prefix"]}{i:0{digits}d}'
                    )
                    i += 1
                file.write(
                    f'<object id="{item["__id"]}" name="{key}" type="{stype if stype else item["__type"]}"/>\n'
                )
            file.write("</objects>\n")

            # Writing detailed information for each object
            file.write("<information>\n")
            # Counter of the number of items that define each object
            i = 0
            # Looping over the items
            for item in self._dict.values():
                # The objects are unique for the combination {jobid,path}
                file.write(f'<info oid="{item["__id"]}" type="short">\n')
                # Looping over the quantities obtained in this item
                for key, value in item.items():
                    # The __nelems_{type} is used to indicate to DBupdate the number of elements - important when the file is empty
                    if key.startswith("__nelems"):
                        file.write(
                            ' <data key={:24s} value="{}"/>\n'.format(
                                '"' + str(key) + '"', value
                            )
                        )
                        continue
                    if key.startswith("__"):
                        continue
                    if value:
                        # if (value) and (value != "0"):
                        # Replacing double quotes with single quotes to avoid problems importing the values
                        file.write(
                            ' <data key={:24s} value="{}"/>\n'.format(
                                '"' + str(key) + '"',
                                (
                                    value.replace('"', "'")
                                    if isinstance(value, str)
                                    else value
                                ),
                            )
                        )
                # if ts:
                #   file.write(" <data key={:24s} value=\"{}\"/>\n".format('\"ts\"',ts))

                file.write(f"</info>\n")
                i += 1

            file.write("</information>\n")
            file.write("</lml:lgui>\n")

        log_continue(self.log, "Finished!")

        return

    def get_node_info(self, prefix="", stype=""):
        """
        Gets information about nodes from Kubernetes API
        """
        config.load_kube_config()
        v1 = client.CoreV1Api()
        nodes = v1.list_node()
        for node in nodes.items:
            current_unit = node.metadata.name
            self.log.debug(f"Parsing units of {current_unit}...\n")
            self._raw[current_unit] = {}
            self._raw[current_unit]["id"] = current_unit
            self._raw[current_unit]["ncores"] = node.status.capacity["cpu"]
            self._raw[current_unit]["features"] = node.metadata.labels
            self._raw[current_unit]["physmem"] = node.status.capacity["memory"]
            self._raw[current_unit]["state"] = node.status.conditions[-1].type
            self._raw[current_unit]["reason"] = node.status.conditions[-1].reason
            self._raw[current_unit]["__prefix"] = prefix
            self._raw[current_unit]["__type"] = stype
        self._dict |= self._raw

    def get_node_alloc(self):
        """
        Gets resources allocated to nodes from Kubernetes API (must be run before get_node_metrics)
        """
        config.load_kube_config()
        v1 = client.CoreV1Api()
        # Prepare dictionaries to hold the total requests per node
        requests_per_node = collections.defaultdict(lambda: {"cpu": 0, "memory": 0})
        pods = v1.list_pod_for_all_namespaces()
        for pod in pods.items:
            node_name = pod.spec.node_name
            if node_name:  # Ensure the pod is assigned to a node
                for container in pod.spec.containers:
                    resources = (
                        container.resources.requests
                        if container.resources and container.resources.requests
                        else {}
                    )
                    requests_per_node[node_name]["cpu"] += parse_resource_value(
                        resources.get("cpu", "0")
                    )
                    requests_per_node[node_name]["memory"] += parse_resource_value(
                        resources.get("memory", "0Mi")
                    )

        # Add the aggregated requests per node to dict
        for node_name, resources in requests_per_node.items():
            self._raw[node_name]["allocmem"] = resources["memory"]

        self._dict |= self._raw

    def get_node_metrics(self):
        """
        Gets metrics about nodes from Kubernetes Metrics API
        """
        config.load_kube_config()
        api = client.CustomObjectsApi()
        node_metrics = api.list_cluster_custom_object(
            "metrics.k8s.io", "v1beta1", "nodes"
        )
        for node in node_metrics.get("items", []):
            print(f"Parsing metrics of node: {node['metadata']['name']}")
            self._raw[node["metadata"]["name"]]["load"] = node["usage"].get("cpu")
            self._raw[node["metadata"]["name"]]["memU"] = node["usage"].get("memory")
            self._raw[node["metadata"]["name"]]["freemem"] = self._raw[
                node["metadata"]["name"]
            ]["allocmem"] - parse_memory_to_bytes(node["usage"].get("memory"))

        self._dict |= self._raw

    def get_job_info(self, prefix="", stype=""):
        """
        Gets information about jobs from Kubernetes API
        """
        config.load_kube_config()
        v1 = client.CoreV1Api()
        pods = v1.list_pod_for_all_namespaces()
        for pod in pods.items:
            pod_name = pod.metadata.name
            self.log.debug(f"Parsing units of pod {pod_name}...\n")
            self._raw[pod_name] = {}
            self._raw[pod_name]["name"] = pod_name
            self._raw[pod_name]["userprio"] = pod.spec.priority
            self._raw[pod_name]["account"] = pod.metadata.namespace
            self._raw[pod_name]["qos"] = pod.status.qos_class
            if pod.status.container_statuses[-1].state.terminated:
                self._raw[pod_name]["state"] = "TERMINATED"
                self._raw[pod_name]["reason"] = pod.status.container_statuses[
                    -1
                ].state.terminated.reason
            elif pod.status.container_statuses[-1].state.running:
                self._raw[pod_name]["state"] = "RUNNING"
                self._raw[pod_name]["reason"] = ""
            elif pod.status.container_statuses[-1].state.waiting:
                self._raw[pod_name]["state"] = "WAITING"
                self._raw[pod_name]["reason"] = pod.status.container_statuses[
                    -1
                ].state.waiting.reason
            self._raw[pod_name]["restart"] = pod.status.container_statuses[
                -1
            ].restart_count
            self._raw[pod_name]["queuedate"] = pod.metadata.creation_timestamp
            self._raw[pod_name]["starttime"] = pod.status.start_time
            self._raw[pod_name]["endtime"] = (
                pod.status.container_statuses[-1].state.terminated.finished_at
                if pod.status.container_statuses[-1].state.terminated
                else "Unknown"
            )
            self._raw[pod_name]["nodelist"] = pod.spec.node_name
            self._raw[pod_name]["command"] = (
                " ".join(pod.spec.containers[0].command)
                if pod.spec.containers[0].command
                else "no commands given"
            )
            self._raw[pod_name]["__type"] = stype
            self._raw[pod_name]["__prefix"] = prefix

        self._dict |= self._raw


def log_continue(log, message):
    """
    Change formatter to write a continuation 'message' on the logger 'log' and then change the format back
    """
    for handler in log.handlers:
        handler.setFormatter(
            CustomFormatter(
                "%(message)s (%(lineno)-3d)[%(asctime)s]\n",
                datefmt=log_config["datefmt"],
            )
        )

    log.info(message)

    for handler in log.handlers:
        handler.setFormatter(
            CustomFormatter(log_config["format"], datefmt=log_config["datefmt"])
        )
    return


def get_system_name(options: dict) -> str:
    """
    Get system name from systemname key of options
    Options that are tested (first string, then in the dictionary order):
      - direct string
        systemname: 'system'
      - file containing system name:
        systemname:
          file: '/path/to/file'
      - environment variable:
        systemname:
          env: 'SYSTEMNAME'
    """
    log = logging.getLogger("logger")

    systemname = "unknown"
    # Checking if systemname key is given in the options
    if "systemname" in options:
        if isinstance(options["systemname"], str):
            # If it's a string, set it as the systemname
            systemname = options["systemname"]
        elif isinstance(options["systemname"], dict):
            # If it's a dict, loop over the keys (but only 'file' or 'env' are recognized)
            for key, value in options["systemname"].items():
                if key == "file":
                    # If file is given, try to read it
                    try:
                        with open(value, "r") as file:
                            systemname = file.read()
                        break  # Stop from trying other ways if file was read
                    except FileNotFoundError:
                        log.error(f"Could not get system name from file {value}\n")
                elif key == "env":
                    # Trying to get from environment variable
                    name = os.environ.get(value)
                    if name:
                        systemname = name
                        break  # Stop from trying other ways if envvar was read
                    else:
                        log.error(
                            f"Could not get system name from environment ${value}\n"
                        )
                else:
                    log.error(f"System name not recognized from {key}:{value}\n")
        else:
            log.error(
                f"Cannot obtain system name from 'systemname' given: {options['systemname']}\n"
            )
    else:
        log.error("System name not defined in 'systename'\n")

    systemname = systemname.strip()
    log.info(f"Using system name: {systemname}\n")
    return systemname


def parse_config_yaml(filename):
    """
    YML configuration parser
    """
    import yaml

    with open(filename, "r") as configyml:
        configyml = yaml.safe_load(configyml)
    return configyml


class CustomFormatter(logging.Formatter):
    """
    Formatter to add colors to log output
    (adapted from https://stackoverflow.com/a/56944256/3142385)
    """

    def __init__(self, fmt, datefmt=""):
        super().__init__()
        self.fmt = fmt
        self.datefmt = datefmt
        # Colors
        self.grey = "\x1b[38;20m"
        self.yellow = "\x1b[93;20m"
        self.blue = "\x1b[94;20m"
        self.magenta = "\x1b[95;20m"
        self.cyan = "\x1b[96;20m"
        self.red = "\x1b[91;20m"
        self.bold_red = "\x1b[91;1m"
        self.reset = "\x1b[0m"
        # self.format = "%(asctime)s %(funcName)-18s(%(lineno)-3d): [%(levelname)-8s] %(message)s"

        self.FORMATS = {
            logging.DEBUG: self.cyan + self.fmt + self.reset,
            logging.INFO: self.grey + self.fmt + self.reset,
            logging.WARNING: self.yellow + self.fmt + self.reset,
            logging.ERROR: self.red + self.fmt + self.reset,
            logging.CRITICAL: self.bold_red + self.fmt + self.reset,
        }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(fmt=log_fmt, datefmt=self.datefmt)
        return formatter.format(record)


# Adapted from: https://stackoverflow.com/a/53257669/3142385
class _ExcludeErrorsFilter(logging.Filter):
    def filter(self, record):
        """Only lets through log messages with log level below ERROR ."""
        return record.levelno < logging.ERROR


log_config = {
    "format": "%(asctime)s %(funcName)-18s(%(lineno)-3d): [%(levelname)-8s] %(message)s",
    "datefmt": "%Y-%m-%d %H:%M:%S",
    # 'file': 'slurm.log',
    # 'filemode': "w",
    "level": "INFO",  # Default value; Options: 'DEBUG', 'INFO', 'WARNING', 'ERROR' from more to less verbose logging
}


def log_init(level):
    """
    Initialize logger
    """

    # Getting logger
    log = logging.getLogger("logger")
    log.setLevel(level if level else log_config["level"])

    # Setup handler (stdout, stderr and file when configured)
    oh = logging.StreamHandler(sys.stdout)
    oh.setLevel(level if level else log_config["level"])
    oh.setFormatter(
        CustomFormatter(log_config["format"], datefmt=log_config["datefmt"])
    )
    oh.addFilter(_ExcludeErrorsFilter())
    oh.terminator = ""
    log.addHandler(
        oh
    )  # add the handler to the logger so records from this process are handled

    eh = logging.StreamHandler(sys.stderr)
    eh.setLevel("ERROR")
    eh.setFormatter(
        CustomFormatter(log_config["format"], datefmt=log_config["datefmt"])
    )
    eh.terminator = ""
    log.addHandler(
        eh
    )  # add the handler to the logger so records from this process are handled

    if "file" in log_config:
        fh = logging.FileHandler(log_config["file"], mode=log_config["filemode"])
        fh.setLevel(level if level else log_config["level"])
        fh.setFormatter(
            CustomFormatter(log_config["format"], datefmt=log_config["datefmt"])
        )
        fh.terminator = ""
        log.addHandler(
            fh
        )  # add the handler to the logger so records from this process are handled

    return


################################################################################
# MAIN PROGRAM:
################################################################################
def main():
    """
    Main program
    """

    # Parse arguments
    parser = argparse.ArgumentParser(description="Slurm Adapter for LLview")
    # parser.add_argument("--LMLjobfile",  default="./jumonc_LML.xml", help="Output LML file for information of jobs")
    parser.add_argument(
        "--config",
        default=False,
        help="YAML config file containing the information to be gathered and converted to LML",
    )
    parser.add_argument(
        "--loglevel",
        default=False,
        help="Select log level: 'DEBUG', 'INFO', 'WARNING', 'ERROR' (more to less verbose)",
    )
    parser.add_argument(
        "--singleLML", default=False, help="Merge all sections into a single LML file"
    )
    parser.add_argument("--outfolder", default=False, help="Reference output folder")
    # parser.add_argument("--PAT_NODES", default=False,      help="Pattern of node names to gather information from")

    args = parser.parse_args()

    # Configuring the logger (level and format)
    log_init(args.loglevel)
    log = logging.getLogger("logger")

    if args.config:
        config = parse_config_yaml(args.config)
    else:
        log.critical("Config file not given!\n")
        parser.print_help()
        exit(1)

    if args.singleLML:
        unique = SlurmInfo()

    #####################################################################################
    # Processing config file
    for key, options in config.items():
        if (not args.singleLML) and ("LML" not in options):
            log.error(
                f"No LML file given for {key} in config file! Skipping section...\n"
            )
            continue

        start_time = time.time()

        # Initializing new object of type given in config
        slurm_info = SlurmInfo()

        # Parsing Slurm output
        if "methods" not in options:
            log.warning(
                f"No 'methods' key given for K8s methods for {key} in config file! Skipping...\n"
            )
        else:
            slurm_info.parse(
                options["methods"],
                prefix=options["prefix"] if "prefix" in options else "i",
                stype=options["type"] if "type" in options else "item",
            )

        # Modifying SLURM output with functions
        if "modify_after_parse" in options:
            slurm_info.modify(options["modify_after_parse"])

        # Using function of name 'key' (current key being processed, e.g.: nodeinfo, jobinfo, etc.), when defined,
        # to modify that particular group/dictionary and items
        if key in globals():
            func = globals()[key]
            slurm_info.add(func(options, slurm_info))

        # Modifying SLURM output with functions
        if "modify_before_mapping" in options:
            slurm_info.modify(options["modify_before_mapping"])

        # Applying pattern to include or exclude units
        if "exclude" in options or "include" in options:
            slurm_info.apply_pattern(
                exclude=options["exclude"] if "exclude" in options else "",
                include=options["include"] if "include" in options else "",
            )

        # Mapping keywords
        if "mapping" in options:
            slurm_info.map(options["mapping"])

        end_time = time.time()
        log.debug(f"Gathering {key} information took {end_time - start_time:.4f}s\n")

        # Add timing key
        # if not slurm_info.empty():
        timing = {}
        name = f"get{key}"
        timing[name] = {}
        timing[name]["startts"] = start_time
        timing[name]["datats"] = start_time
        timing[name]["endts"] = end_time
        timing[name]["duration"] = end_time - start_time
        timing[name]["nelems"] = len(slurm_info)
        # The __nelems_{type} is used to indicate to DBupdate the number of elements - important when the file is empty
        timing[name][f"__nelems_{options['type'] if 'type' in options else 'item'}"] = (
            len(slurm_info)
        )
        timing[name]["__type"] = "pstat"
        timing[name]["__id"] = f"pstat_get{key}"
        slurm_info.add(timing)

        if not args.singleLML:
            if slurm_info.empty():
                log.warning(
                    f"Object {key} is empty, nothing to output to LML! Skipping...\n"
                )
            else:
                slurm_info.to_LML(
                    f"{args.outfolder + '/' if args.outfolder else ''}{options['LML']}"
                )
        else:
            # Accumulating for a single LML
            unique = unique + slurm_info

    if args.singleLML:
        if unique.empty():
            log.warning(
                f"Unique object is empty, nothing to output to LML! Skipping...\n"
            )
        else:
            unique.to_LML(
                f"{args.outfolder + '/' if args.outfolder else ''}{args.singleLML}"
            )
    return


if __name__ == "__main__":
    main()
