`timescale 1ns/1ps
// Tests real signed max/min/add, operands, bypass and output backpressure.
module tb_nvdla_sdp_alu;
  reg nvdla_core_clk = 0;
  always #5 nvdla_core_clk = ~nvdla_core_clk;
  reg nvdla_core_rstn = 0;
  reg [31:0] alu_data_in = 0;
  reg alu_in_pvld = 0, alu_op_pvld = 0, alu_out_prdy = 0;
  reg [1:0] cfg_alu_algo = 0;
  reg cfg_alu_bypass = 0, cfg_alu_src = 0;
  reg [15:0] cfg_alu_op = 0, chn_alu_op = 0;
  reg [5:0] cfg_alu_shift_value = 0;
  wire [32:0] alu_data_out;
  wire alu_in_prdy, alu_op_prdy, alu_out_pvld;
  integer cases = 0;
  NV_NVDLA_SDP_HLS_X_int_alu dut (.*);
  task tick; begin @(posedge nvdla_core_clk); #1; end endtask
  task reset;
    begin
      nvdla_core_rstn = 0; alu_in_pvld = 0; alu_op_pvld = 0; alu_out_prdy = 0;
      repeat (2) tick(); @(negedge nvdla_core_clk); nvdla_core_rstn = 1;
    end
  endtask
  task vector(input signed [31:0] data, input signed [15:0] operand,
              input [1:0] operation, input [5:0] shift,
              input bit channel_operand, input bit bypass,
              input signed [32:0] expected);
    integer waited;
    begin
      if (cases == 0 && $test$plusargs("VWB_NEGATIVE_CONTROL")) expected = expected + 1;
      reset();
      alu_data_in = data; cfg_alu_op = operand; chn_alu_op = operand;
      cfg_alu_algo = operation; cfg_alu_shift_value = shift;
      cfg_alu_src = channel_operand; cfg_alu_bypass = bypass;
      alu_in_pvld = 1; alu_op_pvld = channel_operand;
      #1;
      if (!alu_in_prdy && !bypass) $fatal(1, "ALU not initially ready");
      tick(); @(negedge nvdla_core_clk);
      // Bypass is combinational and remains valid while its producer is held.
      if (!bypass) begin alu_in_pvld = 0; alu_op_pvld = 0; end
      waited = 0;
      while (!alu_out_pvld && waited < 12) begin tick(); waited = waited + 1; end
      if (!alu_out_pvld || alu_data_out !== expected)
        $fatal(1, "ALU case %0d got %h valid=%b expected %h", cases, alu_data_out, alu_out_pvld, expected);
      repeat (3) begin
        tick();
        if (!alu_out_pvld || alu_data_out !== expected) $fatal(1, "ALU stalled result changed");
      end
      @(negedge nvdla_core_clk); alu_out_prdy = 1;
      tick(); @(negedge nvdla_core_clk); alu_in_pvld = 0; alu_op_pvld = 0;
      repeat (4) tick();
      if (alu_out_pvld) $fatal(1, "ALU output duplicated");
      cases = cases + 1;
    end
  endtask
  initial begin
    vector(-32'sd7, 16'sd3, 0, 0, 0, 0, 33'sd3);
    vector(-32'sd7, 16'sd3, 1, 0, 0, 0, -33'sd7);
    vector(32'sd100, -16'sd9, 2, 2, 0, 0, 33'sd64);
    vector(32'sh7fffffff, 16'sd1, 2, 0, 0, 0, 33'sh080000000);
    vector(-32'sd20, -16'sd4, 2, 1, 1, 0, -33'sd28);
    vector(-32'sd123, 16'sd1, 0, 0, 0, 1, -33'sd123);
    $display("VWB_UPSTREAM_COVERAGE nvdla-sdp-alu cases=%0d", cases);
    $display("VWB_UPSTREAM_PASS nvdla-sdp-alu"); $finish;
  end
  initial begin #10000; $fatal(1, "ALU timeout"); end
endmodule
