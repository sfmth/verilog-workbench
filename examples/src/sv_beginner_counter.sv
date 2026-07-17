module sv_beginner_counter (
  input  logic       clk,
  input  logic       reset_n,
  input  logic       enable,
  output logic [3:0] count
);
  always_ff @(posedge clk) begin
    // The _n suffix means reset is active when it is low.
    if (!reset_n)
      count <= 4'b0;
    else if (enable)
      count <= count + 1'b1;
  end
endmodule
