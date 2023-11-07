from abc import abstractmethod

import os
import sys
import glob

from ..hdl import *
from ..lib.cdc import ResetSynchronizer
from ..build import *


__all__ = ["EfinixPlatform"]

# We'll need split our attributes between our port and its various buffers,
# in order to match the format the Efinity wants.

PIN_ATTRS = [
    "io_standard"
]
INPUT_ATTRS = [
    "conn_type",
    "is_register",
    "clock_name",
    "is_clock_inverted",
    "pull_option",
    "is_schmitt_trigger",
    "ddio_type",
]
OUTPUT_ATTRS = [
    "is_clock_inverted",
    "is_slew_rate",
    "clock_name",
    "tied_option",
    "ddio_type",
    "drive_strength",
]
OE_ATTRS = []



class EfinixPlatform(TemplatedPlatform):
    """
    .. rubric:: Efinity toolchain

    Required tools:
        * ``efinity``

    The environment is populated by running the script specified in the environment variable
    ``AMARANTH_ENV_EFINITY``, if present.

    Available overrides:
        * ``add_constraints``: inserts commands in SDC file.
    """

    device    = property(abstractmethod(lambda: None))
    package   = property(abstractmethod(lambda: None))
    speed     = property(abstractmethod(lambda: None))
    family    = property(abstractmethod(lambda: None))
    toolchain = "efinity"

    default_clk = None

    required_tools = [
        "efx_map",
        "efx_pnr",
        "efx_pgm",
    ]

    file_templates = {
        **TemplatedPlatform.build_script_templates,

        # Project file.
        "{{name}}.xml": r"""
           <?xml version="1.0" encoding="UTF-8"?>
            <efx:project name="{{name}}" description="" last_change_date="at January 1 1970 00:00:00" location="." sw_version="2023.1.150" last_run_state="" last_run_tool="" last_run_flow="" config_result_in_sync="true" design_ood="sync" place_ood="" route_ood="sync" xmlns:efx="http://www.efinixinc.com/enf_proj" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.efinixinc.com/enf_proj enf_proj.xsd">
            <efx:device_info>
                <efx:family name="{{platform.family}}"/>
                <efx:device name="{{platform.device}}{{platform.package}}"/>
                <efx:timing_model name="C{{platform.speed}}"/>
            </efx:device_info>
            <efx:design_info def_veri_version="sv_09" def_vhdl_version="vhdl_2008">
                <efx:top_module name="{{name}}"/>
                <efx:design_file name="{{name}}.v" version="default" library="default"/>
                {% for file in platform.iter_files(".v", ".sv", ".vhd", ".vhdl") -%}
                    <efx:design_file name="{{file|ascii_escape}}.v" version="default" library="default"/>
                {% endfor %}
                <efx:top_vhdl_arch name=""/>
            </efx:design_info>
            <efx:constraint_info>
                <efx:sdc_file name="{{name}}.sdc" />
                <efx:inter_file name=""/>
            </efx:constraint_info>
        </efx:project>
        """,
        # Interface constraints.
        # FIXME(ktemkin): fix IOBANK info?
        # FIXME(ktemkin): generate LVDS diffios (and any PLL info that needs to go here?)
        "{{name}}.peri.xml": r"""
           <?xml version="1.0" encoding="UTF-8"?>
            <efxpt:design_db name="{{name}}" device_def="{{platform.device}}{{platform.package}}" location="." version="2023.1.150" db_version="20231999" last_change_date="Sat Nov  4 21:14:37 2023" xmlns:efxpt="http://www.efinixinc.com/peri_design_db" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.efinixinc.com/peri_design_db peri_design_db.xsd ">
                <efxpt:gpio_info device_def="{{platform.device}}{{platform.package}}">
                    {% for port_name, pin_name, direction, lvds, port_attrs, input_attrs, output_attrs, output_enable_attrs in platform.iter_gpio_constraints() %}
                        <efxpt:gpio name="{{pin_name}}" gpio_def="{{port_name}}" mode="{{direction}}" {%- for key, value in port_attrs.items() %} {{key}}="{{value}}"{% endfor %} >
                        {% if direction in ("input", "inout") %}
                            <efxpt:input_config name="{{pin_name}}" {%- for key, value in input_attrs.items() %} {{key}}="{{value}}"{% endfor %} />
                        {% endif %}
                        {% if direction in ("output", "inout") %}
                            <efxpt:output_config name="{{pin_name}}"  {%- for key, value in output_attrs.items() %} {{key}}="{{value}}"{% endfor %} />
                        {% endif %}
                        {% if direction == "inout" %}
                            <efxpt:output_enable_config name="{{port_name}}"  {%- for key, value in output_enable_attrs.items() %} {{key}}="{{value}}"{% endfor %} />
                        {% endif %}
                        </efxpt:gpio>
                    {% endfor %}
                    <efxpt:global_unused_config state="input with weak pullup"/>
                </efxpt:gpio_info>
                <efxpt:pll_info/>
                <efxpt:lvds_info/>
                <efxpt:jtag_info/>
        </efxpt:design_db>
        """,

        # Core generated verilog.
        "{{name}}.v": r"""
            /* {{autogenerated}} */
            {{emit_verilog()}}
        """,
        "{{name}}.debug.v": r"""
            /* {{autogenerated}} */
            {{emit_debug_verilog()}}
        """,

        # Clock constraints.
        "{{name}}.sdc": r"""
            # {{autogenerated}}
            {% for net_signal, port_signal, frequency in platform.iter_clock_constraints() -%}
                {% if port_signal is not none -%}
                    create_clock -period {{100000000/frequency}} {{port_signal.name|ascii_escape}}
                {% endif %}
            {% endfor %}
        """,

        # Wrapper script to help run the platform python, which depends on a more complex
        # environment setup that can be created in Amaranth's PATH-based configuration.
        #
        # Nonetheless, we can't generate the resultant fields without the environment
        # available at-exec-time in the batch script; since values like EFINITY_HOME
        # will often be set using mechanisms like AMARANTH_ENV_EFINITY.
        "run_platform_tool.py": r"""
            # Automatically generated wrapper. Do not edit.
            #    
            import os
            import sys
            import glob
            import subprocess
            
            efinity_home = os.environ['EFINITY_HOME']
            
            # Find the PYTHONHOME and the PYTHON interpreter we need to target,.
            python_glob       = os.path.join(efinity_home, "python*")
            target_pythonhome = glob.glob(python_glob)[0]
            target_python     = os.path.join(target_pythonhome, "bin", "python")
            
            # Build the neccessary environment for the script to run.
            target_env = os.environ.copy()
            target_env["PYTHONHOME"]  = target_pythonhome
            target_env["EFXPT_HOME"]  = os.path.join(efinity_home, "pt")
            target_env["EFXPGM_HOME"] = os.path.join(efinity_home, "pgm")
            target_env["EFXDBG_HOME"] = os.path.join(efinity_home, "debugger")
            
            # Finally, run the target python.
            subprocess.run([
                target_python,
                os.path.join(efinity_home, sys.argv[1]),
                *sys.argv[2:]
            ], env=target_env)
        """
    }

    command_templates = [

        # First, synthesize.
        r"""
        {{invoke_tool("efx_map")}}
            --project "{{name}}" 
            --root "{{name}}" 
            --write-efx-verilog "{{name}}.map.v" 
            --write-premap-module "{{name}}.elab.vdb" 
            --binary-db "{{name}}.vdb" 
            --device "{{platform.device}}" 
            --family "Trion" 
            --work-dir "./work" 
            --output-dir "./outflow" 
            --project-xml "{{name}}.xml" 
            --I "."
            {% if quiet %}
            >NUL
            {% endif %}
        """,

        # Next, convert our I/O description into raw CSV routing constraints,
        # substituting GPIO pin names for device-specific tile locations.
        sys.executable + " -E run_platform_tool.py" + r"""
                scripts/efx_run_pt.py
                "{{name}}"
                "{{platform.family}}"
                "{{platform.device}}{{platform.package}}"
                {% if quiet %}
                >NUL
                {% endif %}
        """,

        # Next, PNR.
        r"""
        {{invoke_tool("efx_pnr")}}
            --circuit "{{name}}" 
            --family "{{platform.family}}" 
            --device "{{platform.device}}{{platform.package}}" 
            --operating_conditions "C{{platform.speed}}" 
            --pack 
            --place 
            --route 
            --vdb_file "work/{{name}}.vdb" 
            --use_vdb_file "on" 
            --place_file "outflow/{{name}}.place"
            --route_file "outflow/{{name}}.route" 
            --sync_file "outflow/{{name}}.interface.csv"
            --seed "1"
            --placer_effort_level "2"
            --max_threads "-1" 
            --work_dir "work"
            --output_dir "out"
            --timing_analysis "on"
            --load_delay_matrix
            {% if quiet %}
            >NUL
            {% endif %}
        """,

        # Finally, generate our resultant bitfile.
        r"""
        {{invoke_tool("efx_pgm")}}
            --source "work/{{name}}.lbf"
            --dest "{{name}}.hex" 
            --device "{{platform.device}}{{platform.package}}" 
            --family "{{platform.family}}" 
            --periph "outflow/{{name}}.lpf" 
            --interface_designer_settings "outflow/{{name}}_or.ini" 
            --enable_external_master_clock "off" 
            --oscillator_clock_divider "DIV8" 
            --active_capture_clk_edge "posedge" 
            --spi_low_power_mode "on" 
            --io_weak_pullup "on" 
            --enable_roms "smart" 
            --mode "active" 
            --width "1" 
            --release_tri_then_reset "on"
            {% if quiet %}
            >NUL
            {% endif %}
        """,
    ]

    def _generate_efinix_attrs(self, res, attrs):
        """ Generates the collections of attributes Efinity expects.

        Attributes for Efinity are spread out over the entire pin definition,
        as well as for each of the input/output/enable definitions for each pin.
        """

        pin_attrs = {}
        in_attrs  = {}
        out_attrs = {}
        oe_attrs  = {}

        # Place each attribute each the appropriate bucket.
        for attr, value in attrs.items():

            if attr in PIN_ATTRS:
                pin_attrs[attr] = value
            if attr in INPUT_ATTRS:
                in_attrs[attr] = value
            if attr in OUTPUT_ATTRS:
                out_attrs[attr] = value
            if attr in OE_ATTRS:
                oe_attrs[attr] = value


        # If the user didn't provide a connection type, generate one for them,
        # as this attribute is required.
        if not 'conn_type' in in_attrs:
            if res.clock:
                in_attrs['conn_type'] = "gclk"
            else:
                in_attrs['conn_type'] = "normal"


        return pin_attrs, in_attrs, out_attrs, oe_attrs


    def _direction_from_resource(self, res):
        """ Returns the Efinity "mode" direction for the given resource. """
        dir = res.ios[0].dir

        if dir in ("oe", "io"):
            return "inout"
        if dir == "o":
            return "output"
        if dir == "i":
            return "input"

        return "none"


    # Specializations that help generate Efinity files.
    def iter_gpio_constraints(self):
        """ Iterates over every port assignment that Efinity consider a raw "GPIO". """

        # FIXME(ktemkin): handle XDR here

        for res, pin, port, attrs in self._ports:

            # Skip anything that's not based on a simple GPIO pin.
            is_differential = isinstance(res.ios[0], DiffPairs)

            # Get any pins associated with the given port.
            pin_names = res.ios[0].map_names(self._conn_pins, res)

            # Split our attributes into things that should apply to pins,
            # and attributes that should be put on their inner direction fields.
            bit_attrs, in_attrs, out_attrs, oe_attrs = self._generate_efinix_attrs(res, attrs)

            # Get our direction "mode", in the format Efinity needs.
            direction = self._direction_from_resource(res)

            # If this is a scalar, output it directly.
            if len(pin_names) == 1:
                yield pin_names[0], port.io.name, direction, is_differential, bit_attrs, in_attrs, out_attrs, oe_attrs

            # Otherwise, iterate over each bit.
            else:
                for bit, pin_name in enumerate(pin_names):
                    yield pin_name, "{}[{}]".format(port.io.name, bit), direction, is_differential, bit_attrs, in_attrs, out_attrs, oe_attrs


    def run_efinity_platform_tool(self, products, *args):
        """ Helper function that runs an Efinity platform tool. """

        import subprocess

        with products.extract("run_platform_tool.py") as helper_script:
            subprocess.check_call([
                sys.executable,
                "-E",
                helper_script,
                *args
            ])


    # Common logic

    @property
    def default_clk_constraint(self):
        # The core clock is fixed at 10kHz.
        if self.default_clk == "oscint":
            return Clock(10e3)

        # Otherwise, assume a default value.
        else:
            return super().default_clk_constraint


    def create_missing_domain(self, name):
        if name == "sync" and self.default_clk is not None:
            m = Module()

            # T4 and T8 devices have a 10kHz internal oscillator that can be used.
            #
            # (The other Trion devices logically have the oscillator too, but it's
            #  either not routed as a clock or disallowed on the larger devices.)
            if self.default_clk == "oscint" and self.device in ("T4", "T8"):
                clk_i = Signal()
                osc = Signal()

                # FIXME(ktemkin): IMPLEMENT THIS
                raise NotImplementedError()
            else:
                clk_i = self.request(self.default_clk).i

            if self.default_rst is not None:
                rst_i = self.request(self.default_rst).i
            else:
                rst_i = Const(0)

            m.domains += ClockDomain("sync")
            m.d.comb += ClockSignal("sync").eq(clk_i)
            m.submodules.reset_sync = ResetSynchronizer(rst_i, domain="sync")

            return m
