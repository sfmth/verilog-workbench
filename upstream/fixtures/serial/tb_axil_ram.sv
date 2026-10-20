// VWB acceptance bench for the unchanged, pinned alexforencich/verilog-axi RTL.
`timescale 1ns/1ps
module tb_axil_ram;
  reg clk=0,rst=1;
  always #5 clk=~clk;
  reg [7:0] awaddr=0,araddr=0;
  reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
  reg [31:0] wdata=0;
  reg [3:0] wstrb=0;
  wire awready,wready,bvalid,arready,rvalid;
  wire [1:0] bresp,rresp;
  wire [31:0] rdata;
  axil_ram #(.DATA_WIDTH(32),.ADDR_WIDTH(8),.STRB_WIDTH(4),.PIPELINE_OUTPUT(1)) dut (
    .clk(clk),.rst(rst),.s_axil_awaddr(awaddr),.s_axil_awprot(3'b0),
    .s_axil_awvalid(awvalid),.s_axil_awready(awready),.s_axil_wdata(wdata),
    .s_axil_wstrb(wstrb),.s_axil_wvalid(wvalid),.s_axil_wready(wready),
    .s_axil_bresp(bresp),.s_axil_bvalid(bvalid),.s_axil_bready(bready),
    .s_axil_araddr(araddr),.s_axil_arprot(3'b0),.s_axil_arvalid(arvalid),
    .s_axil_arready(arready),.s_axil_rdata(rdata),.s_axil_rresp(rresp),
    .s_axil_rvalid(rvalid),.s_axil_rready(rready)
  );
  integer writes=0,reads=0;
  task automatic write_word(input [7:0] address,input [31:0] value,input [3:0] mask);
    begin
      @(negedge clk); awaddr=address;wdata=value;wstrb=mask;awvalid=1;
      // Skew AW and W: no write may be accepted without the data channel.
      repeat (3) begin @(negedge clk); if(awready) $fatal(1,"AXIL accepted incomplete write"); end
      wvalid=1;
      do @(posedge clk); while (!(awready && wready));
      @(negedge clk); awvalid=0;wvalid=0;
      while (!bvalid) @(negedge clk);
      repeat (4) begin
        if (!bvalid || bresp!==2'b00) $fatal(1,"AXIL write response lost under backpressure");
        @(negedge clk);
      end
      bready=1; @(negedge clk); bready=0;
      if (bvalid) $fatal(1,"AXIL write response did not complete");
      writes=writes+1;
    end
  endtask
  task automatic read_word(input [7:0] address,input [31:0] value);
    reg [31:0] expected;
    begin
      expected=value;
      if ($test$plusargs("VWB_NEGATIVE_CONTROL") && reads==0) expected=value^32'h1;
      @(negedge clk); araddr=address;arvalid=1;
      do @(posedge clk); while (!arready);
      @(negedge clk); arvalid=0;
      while (!rvalid) @(negedge clk);
      repeat (5) begin
        if (!rvalid || rresp!==2'b00 || rdata!==expected)
          $fatal(1,"AXIL read mismatch addr=%h got=%h expected=%h",address,rdata,expected);
        @(negedge clk);
      end
      rready=1; @(negedge clk); rready=0;
      if (rvalid) $fatal(1,"AXIL read response did not complete");
      reads=reads+1;
    end
  endtask
  initial begin
    repeat (5) @(negedge clk); rst=0;
    read_word(8'h00,32'h00000000);
    write_word(8'h00,32'h11223344,4'b1111);
    write_word(8'h04,32'h89abcdef,4'b1111);
    read_word(8'h00,32'h11223344);read_word(8'h04,32'h89abcdef);
    write_word(8'h00,32'hff00aa00,4'b0101);
    read_word(8'h00,32'h11003300);
    write_word(8'h00,32'h99887766,4'b0000);
    read_word(8'h00,32'h11003300);read_word(8'h04,32'h89abcdef);
    if(writes!=4 || reads!=6) $fatal(1,"AXIL transaction count mismatch");
    $display("VWB_UPSTREAM_PASS axil-ram"); $finish;
  end
  initial begin #20000;$fatal(1,"AXIL RAM timeout");end
endmodule
