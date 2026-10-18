module tb_m0_sv;
  logic [3:0] a, y;
  integer i;
  m0_sv dut(.a(a), .y(y));
  initial begin
    for(i=0; i<16; i=i+1) begin
      a = i; #1;
      if (y !== ((a + 1) & 15)) $fatal(1, "adder mismatch");
    end
    $display("M0_SYSTEMVERILOG_PASS");
    $finish;
  end
endmodule
