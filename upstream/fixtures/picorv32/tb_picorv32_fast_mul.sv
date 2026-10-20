`timescale 1ns/1ps
// Acceptance bench authored for VWB against unchanged official PicoRV32 RTL.
module tb_picorv32_fast_mul;
  reg clk = 0;
  always #5 clk = ~clk;
  reg resetn = 0, pcpi_valid = 0;
  reg [31:0] pcpi_insn = 0, pcpi_rs1 = 0, pcpi_rs2 = 0;
  wire pcpi_wr, pcpi_wait, pcpi_ready;
  wire [31:0] pcpi_rd;
  integer cases = 0;
  picorv32_pcpi_fast_mul dut (.*);
  task tick; begin @(posedge clk); #1; end endtask
  task vector(input [2:0] operation, input [31:0] a, input [31:0] b, input [31:0] expected);
    integer waited;
    begin
      if (cases == 0 && $test$plusargs("VWB_NEGATIVE_CONTROL")) expected = expected + 1;
      @(negedge clk);
      pcpi_insn = {7'b0000001,5'd2,5'd1,operation,5'd3,7'b0110011};
      pcpi_rs1 = a; pcpi_rs2 = b; pcpi_valid = 1;
      waited = 0;
      while (!pcpi_ready && waited < 140) begin tick(); waited = waited + 1; end
      if (!pcpi_ready || !pcpi_wr || pcpi_rd !== expected)
        $fatal(1, "picorv32-fast-mul case %0d op=%b a=%h b=%h got=%h ready=%b wr=%b expected=%h",
               cases, operation, a, b, pcpi_rd, pcpi_ready, pcpi_wr, expected);
      @(negedge clk); pcpi_valid = 0;
      repeat (6) tick();
      if (pcpi_ready || pcpi_wr) $fatal(1, "picorv32-fast-mul duplicate completion");
      cases = cases + 1;
    end
  endtask
  initial begin
    repeat (4) tick(); @(negedge clk); resetn = 1;
    vector(0, 32'd7, -32'd3, -32'd21);
    vector(0, 32'hffffffff, 32'hffffffff, 32'd1);
    vector(0, 32'h80000000, 32'd2, 32'd0);
    vector(0, 32'd0, 32'hffffffff, 32'd0);
    vector(1, -32'd7, 32'd3, 32'hffffffff);
    vector(1, 32'h80000000, 32'd2, 32'hffffffff);
    vector(1, 32'h80000000, 32'h80000000, 32'h40000000);
    vector(1, 32'h7fffffff, 32'h7fffffff, 32'h3fffffff);
    vector(2, -32'd1, 32'hffffffff, 32'hffffffff);
    vector(2, 32'h80000000, 32'hffffffff, 32'h80000000);
    vector(3, 32'hffffffff, 32'hffffffff, 32'hfffffffe);
    vector(3, 32'h80000000, 32'd2, 32'd1);
    // An ADD encoding must not be claimed by an M-extension coprocessor.
    @(negedge clk); pcpi_insn = 32'h002081b3; pcpi_valid = 1;
    repeat (10) begin tick(); if (pcpi_ready || pcpi_wr || pcpi_wait) $fatal(1, "picorv32-fast-mul accepted an ADD"); end
    $display("VWB_UPSTREAM_COVERAGE picorv32-fast-mul cases=%0d", cases);
    $display("VWB_UPSTREAM_PASS picorv32-fast-mul"); $finish;
  end
  initial begin #100000; $fatal(1, "picorv32-fast-mul timeout"); end
endmodule
