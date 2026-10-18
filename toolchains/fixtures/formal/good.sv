module m0_formal(input wire a, input wire b);
  wire y = a ^ b;
  always @* assert(y == (a ^ b));
endmodule
