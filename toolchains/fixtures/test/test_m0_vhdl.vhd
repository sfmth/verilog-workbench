library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity tb_m0_vhdl is end entity;
architecture test of tb_m0_vhdl is
  signal a, b, y : std_logic := '0';
begin
  dut: entity work.m0_vhdl port map(a=>a, b=>b, y=>y);
  process
    variable value : unsigned(1 downto 0);
  begin
    for i in 0 to 3 loop
      value := to_unsigned(i, 2);
      a <= value(1); b <= value(0);
      wait for 1 ns;
      assert y = (a xor b) report "xor mismatch" severity failure;
    end loop;
    report "M0_VHDL_PASS";
    std.env.finish;
  end process;
end architecture;
