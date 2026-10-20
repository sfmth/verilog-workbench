// VWB acceptance bench for the unchanged, pinned alexforencich/verilog-uart RTL.
`timescale 1ns/1ps
module tb_uart_loopback;
  reg clk=0, rst=1;
  always #5 clk=~clk;
  reg [7:0] tx_data=0;
  reg tx_valid=0, rx_ready=0;
  wire tx_ready, rx_valid, serial, tx_busy, rx_busy, overrun, frame_error;
  wire [7:0] rx_data;
  uart #(.DATA_WIDTH(8)) dut (
    .clk(clk), .rst(rst), .s_axis_tdata(tx_data), .s_axis_tvalid(tx_valid),
    .s_axis_tready(tx_ready), .m_axis_tdata(rx_data), .m_axis_tvalid(rx_valid),
    .m_axis_tready(rx_ready), .rxd(serial), .txd(serial), .tx_busy(tx_busy),
    .rx_busy(rx_busy), .rx_overrun_error(overrun), .rx_frame_error(frame_error),
    .prescale(16'd4)
  );
  integer count=0;
  reg [7:0] values[0:7];
  task automatic exchange(input [7:0] value);
    reg [7:0] expected;
    begin
      expected=value;
      if ($test$plusargs("VWB_NEGATIVE_CONTROL") && count==0) expected=value^8'h01;
      @(negedge clk); tx_data=value; tx_valid=1;
      do @(posedge clk); while (!tx_ready);
      @(negedge clk); tx_valid=0;
      while (!rx_valid) @(negedge clk);
      // Hold the receiver's AXI-stream ready low and verify backpressure.
      repeat (5) begin
        if (!rx_valid || rx_data !== expected)
          $fatal(1,"UART data/valid mismatch sent=%h got=%h expected=%h",value,rx_data,expected);
        if (overrun || frame_error) $fatal(1,"UART unexpected receive error");
        @(negedge clk);
      end
      rx_ready=1;
      @(negedge clk); rx_ready=0;
      if (rx_valid) $fatal(1,"UART receive valid did not clear after transfer");
      count=count+1;
    end
  endtask
  initial begin
    values[0]=8'h00; values[1]=8'h55; values[2]=8'haa; values[3]=8'hff;
    values[4]=8'h01; values[5]=8'h80; values[6]=8'h7e; values[7]=8'h81;
    repeat (5) @(negedge clk); rst=0;
    for (integer index=0;index<8;index=index+1) exchange(values[index]);
    if (count!=8) $fatal(1,"UART transaction count mismatch");
    $display("VWB_UPSTREAM_PASS uart-loopback"); $finish;
  end
  initial begin #200000; $fatal(1,"UART loopback timeout"); end
endmodule
