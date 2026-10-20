`timescale 1ns/1ps
// Covers signed accumulation, 34-bit saturation, 32-bit saturation and rounding.
module tb_nvdla_cacc_int8;
  reg nvdla_core_clk = 0;
  always #5 nvdla_core_clk = ~nvdla_core_clk;
  reg nvdla_core_rstn = 0;
  reg [4:0] cfg_truncate = 0;
  reg [21:0] in_data = 0;
  reg [33:0] in_op = 0;
  reg in_op_valid = 0, in_sel = 0, in_valid = 0;
  wire [31:0] out_final_data;
  wire [33:0] out_partial_data;
  wire out_final_sat, out_final_valid, out_partial_valid;
  integer cases = 0;
  NV_NVDLA_CACC_CALC_int8 dut (.*);
  task tick; begin @(posedge nvdla_core_clk); #1; end endtask
  task vector(input signed [21:0] data, input signed [33:0] operand,
              input bit operand_valid, input bit final_result, input [4:0] truncate,
              input signed [33:0] expected, input bit saturated);
    begin
      if (cases == 0 && $test$plusargs("VWB_NEGATIVE_CONTROL")) expected = expected + 1;
      @(negedge nvdla_core_clk);
      in_data = data; in_op = operand; in_op_valid = operand_valid;
      in_sel = final_result; cfg_truncate = truncate; in_valid = 1;
      tick(); @(negedge nvdla_core_clk); in_valid = 0;
      tick();
      if (final_result) begin
        if (!out_final_valid || out_partial_valid ||
            out_final_data !== expected[31:0] || out_final_sat !== saturated)
          $fatal(1, "CACC final case %0d: got %h sat=%b valid=%b expected %h sat=%b",
                 cases, out_final_data, out_final_sat, out_final_valid, expected[31:0], saturated);
      end else if (!out_partial_valid || out_final_valid || out_partial_data !== expected)
        $fatal(1, "CACC partial case %0d: got %h expected %h", cases, out_partial_data, expected);
      cases = cases + 1;
      tick();
      if (out_final_valid || out_partial_valid) $fatal(1, "CACC valid did not drain");
    end
  endtask
  initial begin
    repeat (2) tick(); @(negedge nvdla_core_clk); nvdla_core_rstn = 1;
    vector(22'sd17, 34'sd25, 1, 0, 0, 34'sd42, 0);
    vector(-22'sd17, 34'sd25, 1, 0, 0, 34'sd8, 0);
    vector(-22'sd17, 34'sd999, 0, 0, 0, -34'sd17, 0);
    vector(22'sd1, 34'sh1ffffffff, 1, 0, 0, 34'sh1ffffffff, 0);
    vector(-22'sd1, -34'sh200000000, 1, 0, 0, -34'sh200000000, 0);
    vector(22'sd17, 34'sd25, 1, 1, 0, 34'sd42, 0);
    vector(22'sd0, 34'sh080000000, 1, 1, 0, 34'sh07fffffff, 1);
    vector(-22'sd1, -34'sh080000000, 1, 1, 0, -34'sh080000000, 1);
    vector(22'sd3, 0, 0, 1, 1, 34'sd2, 0);
    vector(-22'sd3, 0, 0, 1, 1, -34'sd2, 0);
    vector(-22'sd5, 0, 0, 1, 1, -34'sd3, 0);
    vector(22'sd127, 0, 0, 1, 3, 34'sd16, 0);
    $display("VWB_UPSTREAM_COVERAGE nvdla-cacc-int8 cases=%0d", cases);
    $display("VWB_UPSTREAM_PASS nvdla-cacc-int8"); $finish;
  end
  initial begin #5000; $fatal(1, "CACC timeout"); end
endmodule
