`timescale 1ns/1ps
// VWB directed ready/valid scoreboard. Upstream RTL is unchanged.
module tb_axis_stream;
  reg clk=0, async_clk=0, rst=1;
  always #5 clk=~clk;
  always #7 async_clk=~async_clk;
`ifdef AXIS_ASYNC
  wire out_clk=async_clk;
`else
  wire out_clk=clk;
`endif
`ifdef AXIS_ADAPTER
  localparam WIDTH=16;
`else
  localparam WIDTH=8;
`endif
  reg [7:0] s_data=0;
  reg s_valid=0, s_last=0, s_user=0;
  reg [7:0] s_id=0;
  wire s_ready;
  wire [WIDTH-1:0] m_data;
  wire [WIDTH/8-1:0] m_keep;
  wire m_valid,m_last,m_user;
  wire [7:0] m_id;
  reg m_ready=0;
  integer sent=0, received=0, cycles=0, stalls=0;
  reg held=0, source_accepted=0;
  reg [WIDTH-1:0] held_data;
  reg [WIDTH/8-1:0] held_keep;
  reg held_last,held_user;
  reg [7:0] held_id;
  function automatic [7:0] payload(input integer index);
    payload=(index*17+3)&255;
  endfunction
  `VWB_DUT #(
`ifdef AXIS_ADAPTER
    .S_DATA_WIDTH(8), .M_DATA_WIDTH(16),
`else
    .DATA_WIDTH(8),
`endif
`ifdef AXIS_FIFO
    .DEPTH(16),
`endif
`ifdef AXIS_ASYNC
    .DEPTH(16),
`endif
    .ID_ENABLE(1),.USER_ENABLE(1)
  ) dut (
`ifdef AXIS_ASYNC
    .s_clk(clk),.s_rst(rst),.m_clk(out_clk),.m_rst(rst),
    .s_pause_req(1'b0),.m_pause_req(1'b0),
`else
    .clk(clk),.rst(rst),
`endif
`ifdef AXIS_FIFO
    .pause_req(1'b0),
`endif
    .s_axis_tdata(s_data),.s_axis_tkeep(1'b1),.s_axis_tvalid(s_valid),
    .s_axis_tready(s_ready),.s_axis_tlast(s_last),.s_axis_tid(s_id),
    .s_axis_tdest(8'd0),.s_axis_tuser(s_user),
    .m_axis_tdata(m_data),.m_axis_tkeep(m_keep),.m_axis_tvalid(m_valid),
    .m_axis_tready(m_ready),.m_axis_tlast(m_last),.m_axis_tid(m_id),
    .m_axis_tuser(m_user)
  );
  initial begin
    #101 rst=0;
    #100000 $fatal(1,"stream watchdog: sent=%0d received=%0d",sent,received);
  end
  // Hold each input beat until accepted, with deterministic source pauses.
  always @(negedge clk) begin
    if (!rst && (!s_valid || source_accepted)) begin
      s_valid=(sent<70) && ((cycles%5)!=1);
      s_data=payload(sent);
      s_last=(sent%7)==6;
      s_id=sent/7;
      s_user=((sent/7)%2)!=0;
    end
  end
  always @(posedge clk) begin
    source_accepted=!rst && s_valid && s_ready;
    if(source_accepted) sent=sent+1;
  end
  always @(negedge out_clk) begin
    if(!rst) begin
      cycles=cycles+1;
      m_ready=(cycles%11)>=6;
    end
  end
  always @(posedge out_clk) begin : scoreboard
    integer lane;
    reg [7:0] expected;
    if(!rst) begin
      if(held && (!m_valid || m_data!==held_data || m_keep!==held_keep ||
          m_last!==held_last || m_user!==held_user || m_id!==held_id))
        $fatal(1,"output changed under backpressure");
      held=m_valid && !m_ready;
      held_data=m_data; held_keep=m_keep; held_last=m_last; held_user=m_user; held_id=m_id;
      if(held) stalls=stalls+1;
      if(m_valid && m_ready) begin
        if(m_keep==0) $fatal(1,"empty output beat");
        if(m_id!==(received/7) || m_user!==(((received/7)%2)!=0))
          $fatal(1,"frame sideband mismatch at byte %0d",received);
        for(lane=0;lane<WIDTH/8;lane=lane+1) if(m_keep[lane]) begin
          expected=payload(received);
          if($test$plusargs("VWB_NEGATIVE_CONTROL") && received==13) expected=expected^8'h01;
          if(received>=70 || m_data[lane*8+:8]!==expected)
            $fatal(1,"data/order mismatch at byte %0d",received);
          received=received+1;
        end
        if(m_last!==((received%7)==0)) $fatal(1,"frame boundary mismatch");
        if(received==70) begin
          if(stalls==0) $fatal(1,"backpressure coverage missing");
          $display("VWB_UPSTREAM_PASS %s",`VWB_CASE);
          $finish;
        end
      end
    end
  end
endmodule
