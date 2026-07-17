`default_nettype none
`timescale 1ns/1ns


module potential_arithmetic_unit (
    // save
    input wire [255:0] cnn_32n_potential_in,
    output wire [255:0] save_32n_potential_out,

    //load
    input wire [255:0] sram_32n_potential_in,
    input wire [2:0] beta,

    output wire [31:0] spikes,
    input wire [7:0] uthresh


    // input clk, reset
    );

    //spike generate
    genvar h;
    generate
        for (h = 0; h < 32; h = h + 1 ) begin
            assign spikes[h] = (sram_32n_potential_in[(h*8)+7:h*8] > uthresh) ? 1 : 0 ;
        end
    endgenerate

    // save potentials for future use
    genvar i;
    generate
        for (i = 0; i < 32; i = i + 1 ) begin
            assign save_32n_potential_out[(i*8)+7:i*8] = (spikes[i]) ?
                                                            8'b0 :
                                                            mult_ans[(i*8)+7:i*8] +
                                                            cnn_32n_potential_in[(i*8)+7:i*8];
        end
    endgenerate


    //load potentials and apply beta
    wire [255:0] mult_ans;
    genvar j;
    generate
        for (j = 0; j < 32; j = j + 1 ) begin
            shift_add_mult sam(
                .beta({1'b0,beta}),
                .potential_in(sram_32n_potential_in[(j*8)+7:j*8]),
                .mult_ans(mult_ans[(j*8)+7:j*8])
            );
        end
    endgenerate

    // hidden layer mode
    // process spks from input layer
    // genvar j;
    // generate
    //     for (j = 0; j < 16; j = j + 1 ) begin:g_hidden_mode
    //         schmitt_trigger sch0(
    //             .potential_in(hidden_16n_potential_in[(j*8)+7:j*8]),
    //             .spk(hidden_16n_spk_out[j]),
    //             .spkblty_out(hidden_16n_spkblty_out[j]),
    //             .spkblty_in(hidden_16n_spkblty_in[j])
    //         );
    //     end
    // endgenerate

    // //pass spk reads to the hidden layer
    // assign hidden_2n_spk_ac_out = hidden_2n_spk_ac_in;

    // // Input layer mode
    // // load the 1024bit input spk reg for the input layer
    // always @(posedge clk) begin
    //     if (reset) begin
    //         input_1024reg_spk_ac_out <= 0;
    //     end else begin
    //         if (input_128n_spk_in_we) begin
    //             case (input_128n_spk_in_mask)
    //                 0:  input_1024reg_spk_ac_out[127:0] <= input_128n_spk_in;
    //                 1:  input_1024reg_spk_ac_out[255:128] <= input_128n_spk_in;
    //                 2:  input_1024reg_spk_ac_out[383:256] <= input_128n_spk_in;
    //                 3:  input_1024reg_spk_ac_out[511:384] <= input_128n_spk_in;
    //                 4:  input_1024reg_spk_ac_out[639:512] <= input_128n_spk_in;
    //                 5:  input_1024reg_spk_ac_out[767:640] <= input_128n_spk_in;
    //                 6:  input_1024reg_spk_ac_out[895:768] <= input_128n_spk_in;
    //                 7:  input_1024reg_spk_ac_out[1023:896] <= input_128n_spk_in;
    //                 default:    input_1024reg_spk_ac_out <= 1024'bx;
    //             endcase
    //         end
    //     end
	// end

    // // process spk from output layer
    // genvar k;
	// generate
    //     for (k = 0; k < 10; k = k + 1 ) begin:g_input_mode
    //         schmitt_trigger sch1(
    //             .potential_in(input_10n_potential_in[(k*8)+7:k*8]),
    //             .spk(input_10n_spk_out[k]),
    //             .spkblty_out(input_10n_spkblty_out[k]),
    //             .spkblty_in(input_10n_spkblty_in[k])
    //         );
    //     end
    // endgenerate

    //output layer mode

endmodule
