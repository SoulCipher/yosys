#!/usr/bin/env python3
#
# yosys -- Yosys Open SYnthesis Suite
#
# Copyright (C) 2012  Clifford Wolf <clifford@clifford.at>
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
#

import os, sys, getopt, re
##yosys-sys-path##
from smtio import smtio, smtopts, mkvcd
from collections import defaultdict

skip_steps = 0
step_size = 1
num_steps = 20
vcdfile = None
vlogtbfile = None
inconstr = list()
outconstr = None
gentrace = False
tempind = False
dumpall = False
assume_skipped = None
final_only = False
topmod = None
so = smtopts()


def usage():
    print("""
yosys-smtbmc [options] <yosys_smt2_output>

    -t <num_steps>
    -t <skip_steps>:<num_steps>
    -t <skip_steps>:<step_size>:<num_steps>
        default: skip_steps=0, step_size=1, num_steps=20

    -g
        generate an arbitrary trace that satisfies
        all assertions and assumptions.

    -i
        instead of BMC run temporal induction

    -m <module_name>
        name of the top module

    --smtc <constr_filename>
        read constraints file

    --final-only
        only check final constraints, assume base case

    --assume-skipped <start_step>
        assume asserts in skipped steps in BMC.
        no assumptions are created for skipped steps
        before <start_step>.

    --dump-vcd <vcd_filename>
        write trace to this VCD file
        (hint: use 'write_smt2 -wires' for maximum
        coverage of signals in generated VCD file)

    --dump-vlogtb <verilog_filename>
        write trace as Verilog test bench

    --dump-smtc <constr_filename>
        write trace as constraints file

    --dump-all
        when using -g or -i, create a dump file for each
        step. The character '%' is replaces in all dump
        filenames with the step number.
""" + so.helpmsg())
    sys.exit(1)


try:
    opts, args = getopt.getopt(sys.argv[1:], so.shortopts + "t:igm:", so.longopts +
            ["final-only", "assume-skipped=", "smtc=", "dump-vcd=", "dump-vlogtb=", "dump-smtc=", "dump-all"])
except:
    usage()

for o, a in opts:
    if o == "-t":
        a = a.split(":")
        if len(a) == 1:
            num_steps = int(a[0])
        elif len(a) == 2:
            skip_steps = int(a[0])
            num_steps = int(a[1])
        elif len(a) == 3:
            skip_steps = int(a[0])
            step_size = int(a[1])
            num_steps = int(a[2])
        else:
            assert 0
    elif o == "--assume-skipped":
        assume_skipped = int(a)
    elif o == "--final-only":
        final_only = True
    elif o == "--smtc":
        inconstr.append(a)
    elif o == "--dump-vcd":
        vcdfile = a
    elif o == "--dump-vlogtb":
        vlogtbfile = a
    elif o == "--dump-smtc":
        outconstr = a
    elif o == "--dump-all":
        dumpall = True
    elif o == "-i":
        tempind = True
    elif o == "-g":
        gentrace = True
    elif o == "-m":
        topmod = a
    elif so.handle(o, a):
        pass
    else:
        usage()

if len(args) != 1:
    usage()


if tempind and len(inconstr) != 0:
    print("Error: options -i and --smtc are exclusive.");
    sys.exit(1)


constr_final_start = None
constr_asserts = defaultdict(list)
constr_assumes = defaultdict(list)

for fn in inconstr:
    current_states = None
    current_line = 0

    with open(fn, "r") as f:
        for line in f:
            current_line += 1

            if line.startswith("#"):
                continue

            tokens = line.split()

            if len(tokens) == 0:
                continue

            if tokens[0] == "initial":
                current_states = set()
                current_states.add(0)
                continue

            if tokens[0] == "final":
                constr_final = True
                if len(tokens) == 1:
                    current_states = set(["final-%d" % i for i in range(0, num_steps+1)])
                    constr_final_start = 0
                elif len(tokens) == 2:
                    i = int(tokens[1])
                    assert i < 0
                    current_states = set(["final-%d" % i for i in range(-i, num_steps+1)])
                    constr_final_start = -i if constr_final_start is None else min(constr_final_start, -i)
                else:
                    assert 0
                continue
                continue

            if tokens[0] == "state":
                current_states = set()
                for token in tokens[1:]:
                    tok = token.split(":")
                    if len(tok) == 1:
                        current_states.add(int(token))
                    elif len(tok) == 2:
                        lower = int(tok[0])
                        if tok[1] == "*":
                            upper = num_steps
                        else:
                            upper = int(tok[1])
                        for i in range(lower, upper+1):
                            current_states.add(i)
                    else:
                        assert 0
                continue

            if tokens[0] == "always":
                if len(tokens) == 1:
                    current_states = set(range(0, num_steps+1))
                elif len(tokens) == 2:
                    i = int(tokens[1])
                    assert i < 0
                    current_states = set(range(-i, num_steps+1))
                else:
                    assert 0
                continue

            if tokens[0] == "assert":
                assert current_states is not None

                for state in current_states:
                    constr_asserts[state].append(("%s:%d" % (fn, current_line), " ".join(tokens[1:])))

                continue

            if tokens[0] == "assume":
                assert current_states is not None

                for state in current_states:
                    constr_assumes[state].append(("%s:%d" % (fn, current_line), " ".join(tokens[1:])))

                continue

            assert 0


def get_constr_expr(db, state, final=False, getvalues=False):
    if final:
        if ("final-%d" % state) not in db:
            return ([], [], []) if getvalues else "true"
    else:
        if state not in db:
            return ([], [], []) if getvalues else "true"

    netref_regex = re.compile(r'(^|[( ])\[(-?[0-9]+:|)([^\]]+)\](?=[ )]|$)')

    def replace_netref(match):
        state_sel = match.group(2)

        if state_sel == "":
            st = state
        elif state_sel[0] == "-":
            st = state + int(state_sel[:-1])
        else:
            st = int(state_sel[:-1])

        expr = smt.net_expr(topmod, "s%d" % st, smt.get_path(topmod, match.group(3)))

        return match.group(1) + expr

    expr_list = list()
    for loc, expr in db[("final-%d" % state) if final else state]:
        actual_expr = netref_regex.sub(replace_netref, expr)
        if getvalues:
            expr_list.append((loc, expr, actual_expr))
        else:
            expr_list.append(actual_expr)

    if getvalues:
        loc_list, expr_list, acual_expr_list = zip(*expr_list)
        value_list = smt.get_list(acual_expr_list)
        return loc_list, expr_list, value_list

    if len(expr_list) == 0:
        return "true"

    if len(expr_list) == 1:
        return expr_list[0]

    return "(and %s)" % " ".join(expr_list)


smt = smtio(opts=so)

print("%s Solver: %s" % (smt.timestamp(), so.solver))
smt.setup("QF_AUFBV")

with open(args[0], "r") as f:
    for line in f:
        smt.write(line)
        smt.info(line)

if topmod is None:
    topmod = smt.topmod

assert topmod is not None
assert topmod in smt.modinfo


def write_vcd_trace(steps_start, steps_stop, index):
    filename = vcdfile.replace("%", index)
    print("%s Writing trace to VCD file: %s" % (smt.timestamp(), filename))

    with open(filename, "w") as vcd_file:
        vcd = mkvcd(vcd_file)
        path_list = list()

        for netpath in sorted(smt.hiernets(topmod)):
            hidden_net = False
            for n in netpath:
                if n.startswith("$"):
                    hidden_net = True
            if not hidden_net:
                vcd.add_net([topmod] + netpath, smt.net_width(topmod, netpath))
                path_list.append(netpath)

        for i in range(steps_start, steps_stop):
            vcd.set_time(i)
            value_list = smt.get_net_bin_list(topmod, path_list, "s%d" % i)
            for path, value in zip(path_list, value_list):
                vcd.set_net([topmod] + path, value)

        vcd.set_time(steps_stop)


def write_vlogtb_trace(steps_start, steps_stop, index):
    filename = vlogtbfile.replace("%", index)
    print("%s Writing trace to Verilog testbench: %s" % (smt.timestamp(), filename))

    with open(filename, "w") as f:
        print("module testbench;", file=f)
        print("  reg [4095:0] vcdfile;", file=f)
        print("  reg clock = 0, genclock = 1;", file=f)

        primary_inputs = list()
        clock_inputs = set()

        for name in smt.modinfo[topmod].inputs:
            if name in ["clk", "clock", "CLK", "CLOCK"]:
                clock_inputs.add(name)
            width = smt.modinfo[topmod].wsize[name]
            primary_inputs.append((name, width))

        for name, width in primary_inputs:
            if name in clock_inputs:
                print("  wire [%d:0] PI_%s = clock;" % (width-1, name), file=f)
            else:
                print("  reg [%d:0] PI_%s;" % (width-1, name), file=f)

        print("  %s UUT (" % topmod, file=f)
        for i in range(len(primary_inputs)):
            name, width = primary_inputs[i]
            last_pi = i+1 == len(primary_inputs)
            print("    .%s(PI_%s)%s" % (name, name, "" if last_pi else ","), file=f)
        print("  );", file=f)

        print("  initial begin", file=f)
        print("    if ($value$plusargs(\"vcd=%s\", vcdfile)) begin", file=f)
        print("      $dumpfile(vcdfile);", file=f)
        print("      $dumpvars(0, testbench);", file=f)
        print("    end", file=f)
        print("    while (genclock) begin", file=f)
        print("      #5; clock = 0;", file=f)
        print("      #5; clock = 1;", file=f)
        print("    end", file=f)
        print("  end", file=f)

        print("  initial begin", file=f)

        regs = sorted(smt.hiernets(topmod, regs_only=True))
        regvals = smt.get_net_bin_list(topmod, regs, "s%d" % steps_start)

        print("    #1;", file=f);
        for reg, val in zip(regs, regvals):
            hidden_net = False
            for n in reg:
                if n.startswith("$"):
                    hidden_net = True
            print("    %sUUT.%s = %d'b%s;" % ("// " if hidden_net else "", ".".join(reg), len(val), val), file=f)

        mems = sorted(smt.hiermems(topmod))
        for mempath in mems:
            abits, width, ports = smt.mem_info(topmod, "s%d" % steps_start, mempath)
            mem = smt.mem_expr(topmod, "s%d" % steps_start, mempath)

            addr_expr_list = list()
            for i in range(steps_start, steps_stop):
                for j in range(ports):
                    addr_expr_list.append(smt.mem_expr(topmod, "s%d" % i, mempath, j))

            addr_list = set()
            for val in smt.get_list(addr_expr_list):
                addr_list.add(smt.bv2int(val))

            expr_list = list()
            for i in addr_list:
                expr_list.append("(select %s #b%s)" % (mem, format(i, "0%db" % abits)))

            for i, val in zip(addr_list, smt.get_list(expr_list)):
                val = smt.bv2bin(val)
                print("    UUT.%s[%d] = %d'b%s;" % (".".join(mempath), i, len(val), val), file=f)

        for i in range(steps_start, steps_stop):
            pi_names = [[name] for name, _ in primary_inputs if name not in clock_inputs]
            pi_values = smt.get_net_bin_list(topmod, pi_names, "s%d" % i)

            print("    #1;", file=f);
            print("    // state %d" % i, file=f);
            if i > 0:
                print("    @(posedge clock);", file=f);
            for name, val in zip(pi_names, pi_values):
                print("    PI_%s <= %d'b%s;" % (".".join(name), len(val), val), file=f)

        print("    genclock = 0;", file=f);
        print("  end", file=f)

        print("endmodule", file=f)


def write_constr_trace(steps_start, steps_stop, index):
    filename = outconstr.replace("%", index)
    print("%s Writing trace to constraints file: %s" % (smt.timestamp(), filename))

    with open(filename, "w") as f:
        primary_inputs = list()

        for name in smt.modinfo[topmod].inputs:
            width = smt.modinfo[topmod].wsize[name]
            primary_inputs.append((name, width))


        if steps_start == 0:
            print("initial", file=f)
        else:
            print("state %d" % steps_start, file=f)

        regnames = sorted(smt.hiernets(topmod, regs_only=True))
        regvals = smt.get_net_list(topmod, regnames, "s%d" % steps_start)

        for name, val in zip(regnames, regvals):
            print("assume (= [%s] %s)" % (".".join(name), val), file=f)

        mems = sorted(smt.hiermems(topmod))
        for mempath in mems:
            abits, width, ports = smt.mem_info(topmod, "s%d" % steps_start, mempath)
            mem = smt.mem_expr(topmod, "s%d" % steps_start, mempath)

            addr_expr_list = list()
            for i in range(steps_start, steps_stop):
                for j in range(ports):
                    addr_expr_list.append(smt.mem_expr(topmod, "s%d" % i, mempath, j))

            addr_list = set()
            for val in smt.get_list(addr_expr_list):
                addr_list.add(smt.bv2int(val))

            expr_list = list()
            for i in addr_list:
                expr_list.append("(select %s #b%s)" % (mem, format(i, "0%db" % abits)))

            for i, val in zip(addr_list, smt.get_list(expr_list)):
                print("assume (= (select [%s] #b%s) %s)" % (".".join(mempath), format(i, "0%db" % abits), val), file=f)


        for k in range(steps_start, steps_stop):
            print("", file=f)
            print("state %d" % k, file=f)

            pi_names = [[name] for name, _ in sorted(primary_inputs)]
            pi_values = smt.get_net_list(topmod, pi_names, "s%d" % k)

            for name, val in zip(pi_names, pi_values):
                print("assume (= [%s] %s)" % (".".join(name), val), file=f)


def write_trace(steps_start, steps_stop, index):
    if vcdfile is not None:
        write_vcd_trace(steps_start, steps_stop, index)

    if vlogtbfile is not None:
        write_vlogtb_trace(steps_start, steps_stop, index)

    if outconstr is not None:
        write_constr_trace(steps_start, steps_stop, index)


def print_failed_asserts_worker(mod, state, path):
    assert mod in smt.modinfo

    if smt.get("(|%s_a| %s)" % (mod, state)) == "true":
        return

    for cellname, celltype in smt.modinfo[mod].cells.items():
        print_failed_asserts_worker(celltype, "(|%s_h %s| %s)" % (mod, cellname, state), path + "." + cellname)

    for assertfun, assertinfo in smt.modinfo[mod].asserts.items():
        if smt.get("(|%s| %s)" % (assertfun, state)) == "false":
            print("%s Assert failed in %s: %s" % (smt.timestamp(), path, assertinfo))


def print_failed_asserts(state, final=False):
    loc_list, expr_list, value_list = get_constr_expr(constr_asserts, state, final=final, getvalues=True)

    for loc, expr, value in zip(loc_list, expr_list, value_list):
        if smt.bv2int(value) == 0:
            print("%s Assert %s failed: %s" % (smt.timestamp(), loc, expr))

    if not final:
        print_failed_asserts_worker(topmod, "s%d" % state, topmod)


def print_anyconsts_worker(mod, state, path):
    assert mod in smt.modinfo

    for cellname, celltype in smt.modinfo[mod].cells.items():
        print_anyconsts_worker(celltype, "(|%s_h %s| %s)" % (mod, cellname, state), path + "." + cellname)

    for fun, info in smt.modinfo[mod].anyconsts.items():
        print("%s Value for anyconst in %s (%s): %d" % (smt.timestamp(), path, info, smt.bv2int(smt.get("(|%s| %s)" % (fun, state)))))


def print_anyconsts(state):
    print_anyconsts_worker(topmod, "s%d" % state, topmod)


if tempind:
    retstatus = False
    skip_counter = step_size
    for step in range(num_steps, -1, -1):
        smt.write("(declare-fun s%d () %s_s)" % (step, topmod))
        smt.write("(assert (%s_u s%d))" % (topmod, step))
        smt.write("(assert (%s_h s%d))" % (topmod, step))
        smt.write("(assert (not (%s_is s%d)))" % (topmod, step))

        if step == num_steps:
            smt.write("(assert (not (%s_a s%d)))" % (topmod, step))

        else:
            smt.write("(assert (%s_t s%d s%d))" % (topmod, step, step+1))
            smt.write("(assert (%s_a s%d))" % (topmod, step))

        if step > num_steps-skip_steps:
            print("%s Skipping induction in step %d.." % (smt.timestamp(), step))
            continue

        skip_counter += 1
        if skip_counter < step_size:
            print("%s Skipping induction in step %d.." % (smt.timestamp(), step))
            continue

        skip_counter = 0
        print("%s Trying induction in step %d.." % (smt.timestamp(), step))

        if smt.check_sat() == "sat":
            if step == 0:
                print("%s Temporal induction failed!" % smt.timestamp())
                print_anyconsts(num_steps)
                print_failed_asserts(num_steps)
                write_trace(step, num_steps+1, '%')

            elif dumpall:
                print_anyconsts(num_steps)
                print_failed_asserts(num_steps)
                write_trace(step, num_steps+1, "%d" % step)

        else:
            print("%s Temporal induction successful." % smt.timestamp())
            retstatus = True
            break


else: # not tempind
    step = 0
    retstatus = True
    while step < num_steps:
        smt.write("(declare-fun s%d () %s_s)" % (step, topmod))
        smt.write("(assert (%s_u s%d))" % (topmod, step))
        smt.write("(assert (%s_h s%d))" % (topmod, step))
        smt.write("(assert %s)" % get_constr_expr(constr_assumes, step))

        if step == 0:
            smt.write("(assert (%s_i s0))" % (topmod))
            smt.write("(assert (%s_is s0))" % (topmod))

        else:
            smt.write("(assert (%s_t s%d s%d))" % (topmod, step-1, step))
            smt.write("(assert (not (%s_is s%d)))" % (topmod, step))

        if step < skip_steps:
            if assume_skipped is not None and step >= assume_skipped:
                print("%s Skipping step %d (and assuming pass).." % (smt.timestamp(), step))
                smt.write("(assert (%s_a s%d))" % (topmod, step))
                smt.write("(assert %s)" % get_constr_expr(constr_asserts, step))
            else:
                print("%s Skipping step %d.." % (smt.timestamp(), step))
            step += 1
            continue

        last_check_step = step
        for i in range(1, step_size):
            if step+i < num_steps:
                smt.write("(declare-fun s%d () %s_s)" % (step+i, topmod))
                smt.write("(assert (%s_u s%d))" % (topmod, step+i))
                smt.write("(assert (%s_h s%d))" % (topmod, step+i))
                smt.write("(assert (%s_t s%d s%d))" % (topmod, step+i-1, step+i))
                smt.write("(assert %s)" % get_constr_expr(constr_assumes, step+i))
                last_check_step = step+i

        if not gentrace:
            if not final_only:
                if last_check_step == step:
                    print("%s Checking asserts in step %d.." % (smt.timestamp(), step))
                else:
                    print("%s Checking asserts in steps %d to %d.." % (smt.timestamp(), step, last_check_step))
                smt.write("(push 1)")

                smt.write("(assert (not (and %s)))" % " ".join(["(%s_a s%d)" % (topmod, i) for i in range(step, last_check_step+1)] +
                        [get_constr_expr(constr_asserts, i) for i in range(step, last_check_step+1)]))

                if smt.check_sat() == "sat":
                    print("%s BMC failed!" % smt.timestamp())
                    print_anyconsts(step)
                    for i in range(step, last_check_step+1):
                        print_failed_asserts(i)
                    write_trace(0, last_check_step+1, '%')
                    retstatus = False
                    break

                smt.write("(pop 1)")

            for i in range(step, last_check_step+1):
                smt.write("(assert (%s_a s%d))" % (topmod, i))
                smt.write("(assert %s)" % get_constr_expr(constr_asserts, i))

            if constr_final_start is not None:
                for i in range(step, last_check_step+1):
                    if i < constr_final_start:
                        continue

                    print("%s Checking final constraints in step %d.." % (smt.timestamp(), i))
                    smt.write("(push 1)")

                    smt.write("(assert %s)" % get_constr_expr(constr_assumes, i, final=True))
                    smt.write("(assert (not %s))" % get_constr_expr(constr_asserts, i, final=True))

                    if smt.check_sat() == "sat":
                        print("%s BMC failed!" % smt.timestamp())
                        print_anyconsts(i)
                        print_failed_asserts(i, final=True)
                        write_trace(0, i+1, '%')
                        retstatus = False
                        break

                    smt.write("(pop 1)")
                if not retstatus:
                    break

        else: # gentrace
            for i in range(step, last_check_step+1):
                smt.write("(assert (%s_a s%d))" % (topmod, i))
                smt.write("(assert %s)" % get_constr_expr(constr_asserts, i))

            print("%s Solving for step %d.." % (smt.timestamp(), last_check_step))
            if smt.check_sat() != "sat":
                print("%s No solution found!" % smt.timestamp())
                retstatus = False
                break

            elif dumpall:
                print_anyconsts(0)
                write_trace(0, last_check_step+1, "%d" % step)

        step += step_size

    if gentrace:
        print_anyconsts(0)
        write_trace(0, num_steps, '%')


smt.write("(exit)")
smt.wait()

print("%s Status: %s" % (smt.timestamp(), "PASSED" if retstatus else "FAILED (!)"))
sys.exit(0 if retstatus else 1)

