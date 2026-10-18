module tb_m0_gate;
  reg a, b;
  wire y;
  integer i;
  m0_gate dut(.a(a), .b(b), .y(y));
  initial begin
    for(i=0; i<4; i=i+1) begin
      {a,b} = i; #1;
      if (y !== (a ^ b)) $fatal(1, "xor mismatch");
    end
    $display("M0_VERILOG_PASS");
    $finish;
  end
endmodule
