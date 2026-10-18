// Build-only fixture. Pins follow the repository's existing pack validator.
// A successful pack is not evidence that hardware programming was tested.
module m0_fpga(input wire clock, output reg led);
  initial led = 1'b0;
  always @(posedge clock) led <= ~led;
endmodule
