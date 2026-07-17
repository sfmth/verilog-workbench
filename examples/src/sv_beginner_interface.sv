`include "beginner_bus_helpers.svh"

module sv_beginner_interface (
  input  logic [7:0] value_in,
  output logic [7:0] value_out
);
  beginner_bus_if bus();

  beginner_bus_producer producer (
    .value(value_in),
    .bus(bus)
  );

  beginner_bus_consumer consumer (
    .bus(bus),
    .value(value_out)
  );
endmodule
