module sv_beginner_shift_register (
  input  logic       clk,
  input  logic       reset,
  input  logic       serial_in,
  output logic [3:0] data_out
);
  always_ff @(posedge clk or posedge reset) begin
    // This reset is asynchronous, so it does not wait for a clock edge.
    if (reset)
      data_out <= 4'b0;
    else
      data_out <= {data_out[2:0], serial_in};
  end
endmodule
