interface beginner_bus_if;
  logic [7:0] data;

  modport producer(output data);
  modport consumer(input data);
endinterface

module beginner_bus_producer (
  input logic [7:0] value,
  beginner_bus_if.producer bus
);
  assign bus.data = value;
endmodule

module beginner_bus_consumer (
  beginner_bus_if.consumer bus,
  output logic [7:0] value
);
  assign value = bus.data;
endmodule
