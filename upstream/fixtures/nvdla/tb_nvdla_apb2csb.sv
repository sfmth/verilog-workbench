`timescale 1ns/1ps
// Acceptance bench authored for VWB. The DUT is unmodified official NVDLA RTL.
module tb_nvdla_apb2csb;
  reg pclk = 0;
  always #5 pclk = ~pclk;
  reg prstn = 0, psel = 0, penable = 0, pwrite = 0;
  reg csb2nvdla_ready = 0, nvdla2csb_valid = 0;
  reg [31:0] paddr = 0, pwdata = 0, nvdla2csb_data = 0;
  wire [31:0] prdata, csb2nvdla_wdat;
  wire [15:0] csb2nvdla_addr;
  wire pready, csb2nvdla_valid, csb2nvdla_write, csb2nvdla_nposted;
  NV_NVDLA_apb2csb dut (.*);
  task tick; begin @(posedge pclk); #1; end endtask
  task check(input bit condition, input [255:0] description);
    if (!condition) $fatal(1, "APB2CSB: %0s", description);
  endtask
  initial begin
    repeat (2) tick();
    @(negedge pclk); prstn = 1;
    // APB setup must not emit a CSB transaction.
    psel = 1; pwrite = 1; paddr = 32'h00012340; pwdata = 32'hcafe1234;
    #1; check(!csb2nvdla_valid, "setup emitted a transaction");
    penable = 1;
    #1; check(csb2nvdla_valid && !pready, "write stall missing");
    check(csb2nvdla_addr == ($test$plusargs("VWB_NEGATIVE_CONTROL") ? 16'h48d1 : 16'h48d0) && csb2nvdla_wdat == 32'hcafe1234,
          "address/data conversion wrong");
    check(csb2nvdla_write && !csb2nvdla_nposted, "write control wrong");
    tick(); check(csb2nvdla_valid && !pready, "stalled write lost");
    @(negedge pclk); csb2nvdla_ready = 1;
    #1; check(pready, "ready write did not complete");
    tick(); @(negedge pclk); psel = 0; penable = 0;
    // Read request may be accepted before its response; it must not repeat.
    tick(); @(negedge pclk);
    psel = 1; penable = 1; pwrite = 0; paddr = 32'h40;
    #1; check(csb2nvdla_valid && !pready && !csb2nvdla_write, "read request wrong");
    tick(); check(!csb2nvdla_valid && !pready, "read repeated before response");
    repeat (3) begin tick(); check(!csb2nvdla_valid && !pready, "read wait unstable"); end
    @(negedge pclk); nvdla2csb_valid = 1; nvdla2csb_data = 32'h12345678;
    #1; check(pready && prdata == 32'h12345678, "read response missing");
    tick(); @(negedge pclk); psel = 0; penable = 0; nvdla2csb_valid = 0;
    tick(); check(!csb2nvdla_valid, "idle generated a request");
    $display("VWB_UPSTREAM_PASS nvdla-apb2csb"); $finish;
  end
  initial begin #2000; $fatal(1, "APB2CSB timeout"); end
endmodule
