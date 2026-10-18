module m0_formal(input wire a, input wire b);
  wire y = a ^ b;
  // Deliberately false for a!=b: must produce FAIL plus a nonempty VCD.
  always @* assert(y == (a & b));
endmodule
