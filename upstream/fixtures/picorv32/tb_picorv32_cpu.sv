`timescale 1ns/1ps
// Hand-encoded RV32IM program: no network access or compiler is needed to run it.
// Instruction words are constructed from the ISA's instruction fields below.
module tb_picorv32_cpu;
  reg clk = 0;
  always #5 clk = ~clk;
  reg resetn = 0;
  wire trap, mem_valid, mem_instr;
  reg mem_ready = 0;
  wire [31:0] mem_addr, mem_wdata;
  wire [3:0] mem_wstrb;
  reg [31:0] mem_rdata = 0;
  reg [31:0] memory [0:255];
  reg [31:0] expected [0:6];
  integer i, delay_count = 0, signatures = 0, reads = 0, stalls = 0;
  reg stalled = 0;
  reg [31:0] held_addr, held_wdata;
  reg [3:0] held_wstrb;
  reg held_instr;
  picorv32 #(.ENABLE_MUL(1), .ENABLE_DIV(1), .PROGADDR_RESET(0)) dut (
    .clk(clk), .resetn(resetn), .trap(trap), .mem_valid(mem_valid),
    .mem_instr(mem_instr), .mem_ready(mem_ready), .mem_addr(mem_addr),
    .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
    .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0), .irq(32'b0)
  );
  function [31:0] itype(input integer imm, input [4:0] rs1, input [2:0] op,
                         input [4:0] rd, input [6:0] opcode);
    itype = {imm[11:0], rs1, op, rd, opcode};
  endfunction
  function [31:0] rtype(input [6:0] ext, input [4:0] rs2, input [4:0] rs1,
                         input [2:0] op, input [4:0] rd);
    rtype = {ext, rs2, rs1, op, rd, 7'b0110011};
  endfunction
  function [31:0] store(input integer imm, input [4:0] rs2, input [4:0] rs1);
    store = {imm[11:5], rs2, rs1, 3'b010, imm[4:0], 7'b0100011};
  endfunction
  function [31:0] bne(input integer offset, input [4:0] rs2, input [4:0] rs1);
    bne = {offset[12], offset[10:5], rs2, rs1, 3'b001, offset[4:1], offset[11], 7'b1100011};
  endfunction
  initial begin
    for (i = 0; i < 256; i = i + 1) memory[i] = 32'h00100073; // EBREAK
    memory[0] = itype(7, 0, 0, 1, 7'h13);       // addi x1,x0,7
    memory[1] = itype(-3, 0, 0, 2, 7'h13);      // addi x2,x0,-3
    memory[2] = rtype(0, 2, 1, 0, 3);           // add x3,x1,x2 = 4
    memory[3] = rtype(7'h20, 2, 1, 0, 4);       // sub x4,x1,x2 = 10
    memory[4] = itype(3, 3, 1, 5, 7'h13);       // slli x5,x3,3 = 32
    memory[5] = rtype(0, 1, 5, 4, 6);           // xor x6,x5,x1 = 39
    memory[6] = itype(512, 0, 0, 10, 7'h13);    // addi x10,x0,512
    memory[7] = store(0, 6, 10);                // sw x6,0(x10)
    memory[8] = itype(0, 10, 2, 7, 7'h03);      // lw x7,0(x10)
    memory[9] = bne(56, 6, 7);                 // bne x7,x6,fail
    memory[10] = rtype(1, 2, 1, 0, 8);          // mul x8,x1,x2 = -21
    memory[11] = rtype(1, 2, 8, 4, 9);          // div x9,x8,x2 = 7
    memory[12] = rtype(0, 9, 7, 0, 11);         // add x11,x7,x9 = 46
    memory[13] = itype(46, 0, 0, 12, 7'h13);    // addi x12,x0,46
    memory[14] = bne(36, 12, 11);               // bne x11,x12,fail
    memory[15] = store(256, 3, 10);
    memory[16] = store(260, 4, 10);
    memory[17] = store(264, 5, 10);
    memory[18] = store(268, 6, 10);
    memory[19] = store(272, 8, 10);
    memory[20] = store(276, 9, 10);
    memory[21] = store(280, 11, 10);
    memory[22] = 32'h0000006f;                  // jal x0,0 (unreachable after final signature)
    memory[23] = store(256, 0, 10);             // fail: wrong first signature
    expected[0] = 4; expected[1] = 10; expected[2] = 32; expected[3] = 39;
    expected[4] = -21; expected[5] = 7; expected[6] = $test$plusargs("VWB_NEGATIVE_CONTROL") ? 47 : 46;
    repeat (8) @(negedge clk); resetn = 1;
  end
  // Deliberately wait two clocks per memory request, exercising the native bus.
  always @(posedge clk) begin
    mem_ready <= 0;
    if (resetn) begin
      if (trap) $fatal(1, "CPU trapped before all signatures");
      if (stalled && (!mem_valid || mem_addr !== held_addr || mem_wstrb !== held_wstrb ||
                      mem_instr !== held_instr || (|held_wstrb && mem_wdata !== held_wdata)))
        $fatal(1, "CPU changed a stalled memory request");
      stalled = mem_valid && !mem_ready;
      held_addr = mem_addr; held_wdata = mem_wdata; held_wstrb = mem_wstrb; held_instr = mem_instr;
      if (mem_valid && !mem_ready) begin
        if (delay_count < 2) begin delay_count = delay_count + 1; stalls = stalls + 1; end
        else begin
          delay_count = 0; mem_ready <= 1;
          if (mem_addr >= 1024 || mem_addr[1:0] != 0) $fatal(1, "CPU invalid bus address %h", mem_addr);
          mem_rdata <= memory[mem_addr >> 2];
          if (|mem_wstrb) begin
            if (mem_wstrb != 4'hf || mem_instr) $fatal(1, "CPU malformed word store");
            memory[mem_addr >> 2] <= mem_wdata;
            if (mem_addr >= 768) begin
              if (signatures >= 7 || mem_addr !== 768 + signatures * 4 || mem_wdata !== expected[signatures])
                $fatal(1, "CPU signature %0d addr=%h got=%h expected=%h", signatures, mem_addr, mem_wdata, expected[signatures]);
              signatures = signatures + 1;
              if (signatures == 7) begin
                if (reads < 1 || stalls < 20 || memory[128] !== 39) $fatal(1, "CPU memory coverage missing");
                $display("VWB_UPSTREAM_COVERAGE picorv32-cpu signatures=%0d data_reads=%0d stalls=%0d", signatures, reads, stalls);
    $display("VWB_UPSTREAM_PASS picorv32-cpu");
                $finish;
              end
            end
          end else if (!mem_instr) reads = reads + 1;
        end
      end
    end
  end
  initial begin #200000; $fatal(1, "CPU timeout"); end
endmodule
