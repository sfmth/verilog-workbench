`timescale 1ns/1ps
// Tests signed products, channel operands, PReLU, bypass and output stalls.
module tb_nvdla_sdp_mul;
  reg nvdla_core_clk = 0;
  always #5 nvdla_core_clk = ~nvdla_core_clk;
  reg nvdla_core_rstn = 0;
  reg [32:0] alu_data_out = 0;
  reg alu_out_pvld = 0, cfg_mul_bypass = 0, cfg_mul_prelu = 0, cfg_mul_src = 0;
  reg [15:0] cfg_mul_op = 0, chn_mul_op = 0;
  reg mul_op_pvld = 0, mul_out_prdy = 0;
  wire alu_out_prdy, bypass_trt_out, mul_op_prdy, mul_out_pvld;
  wire [48:0] mul_data_out;
  integer cases = 0;
  NV_NVDLA_SDP_HLS_X_int_mul dut (.*);
  task tick; begin @(posedge nvdla_core_clk); #1; end endtask
  task reset;
    begin
      nvdla_core_rstn = 0; alu_out_pvld = 0; mul_op_pvld = 0; mul_out_prdy = 0;
      repeat (2) tick(); @(negedge nvdla_core_clk); nvdla_core_rstn = 1;
    end
  endtask
  task vector(input signed [32:0] data, input signed [15:0] operand,
              input bit channel_operand, input bit prelu, input bit bypass,
              input signed [48:0] expected, input bit expected_bypass);
    integer waited;
    begin
      if (cases == 0 && $test$plusargs("VWB_NEGATIVE_CONTROL")) expected = expected + 1;
      reset();
      alu_data_out = data; cfg_mul_op = operand; chn_mul_op = operand;
      cfg_mul_src = channel_operand; cfg_mul_prelu = prelu; cfg_mul_bypass = bypass;
      alu_out_pvld = 1; mul_op_pvld = channel_operand;
      #1;
      if (!alu_out_prdy && !bypass) $fatal(1, "MUL not initially ready");
      tick(); @(negedge nvdla_core_clk);
      if (!bypass) begin alu_out_pvld = 0; mul_op_pvld = 0; end
      waited = 0;
      while (!mul_out_pvld && waited < 12) begin tick(); waited = waited + 1; end
      if (!mul_out_pvld || mul_data_out !== expected || bypass_trt_out !== expected_bypass)
        $fatal(1, "MUL case %0d got %h bypass=%b valid=%b expected %h bypass=%b",
               cases, mul_data_out, bypass_trt_out, mul_out_pvld, expected, expected_bypass);
      repeat (3) begin
        tick();
        if (!mul_out_pvld || mul_data_out !== expected || bypass_trt_out !== expected_bypass)
          $fatal(1, "MUL stalled result changed");
      end
      @(negedge nvdla_core_clk); mul_out_prdy = 1;
      tick(); @(negedge nvdla_core_clk); alu_out_pvld = 0; mul_op_pvld = 0;
      repeat (4) tick();
      if (mul_out_pvld) $fatal(1, "MUL output duplicated");
      cases = cases + 1;
    end
  endtask
  initial begin
    vector(33'sd7, 16'sd3, 0, 0, 0, 49'sd21, 0);
    vector(-33'sd7, 16'sd3, 0, 0, 0, -49'sd21, 0);
    vector(-33'sd7, -16'sd3, 1, 0, 0, 49'sd21, 0);
    vector(33'sh0ffffffff, 16'sh7fff, 0, 0, 0, 49'sh07ffeffff8001, 0);
    vector(33'sd19, -16'sd3, 0, 1, 0, 49'sd19, 1);
    vector(-33'sd19, -16'sd3, 0, 1, 0, 49'sd57, 0);
    vector(-33'sd123, 16'sd1, 0, 0, 1, -49'sd123, 0);
    $display("VWB_UPSTREAM_COVERAGE nvdla-sdp-mul cases=%0d", cases);
    $display("VWB_UPSTREAM_PASS nvdla-sdp-mul"); $finish;
  end
  initial begin #10000; $fatal(1, "MUL timeout"); end
endmodule
