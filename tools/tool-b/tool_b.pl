#!/usr/bin/env perl
# tool_b.pl – Main script for tool-b
use strict;
use warnings;
use Getopt::Long qw(GetOptions);
use FindBin qw($Bin);
use lib "$Bin/../../lib/perl";
use CommonUtils qw(log_msg);

my $verbose = 0;
GetOptions(
    'verbose|v' => \$verbose,
    'help|h'    => sub { usage(); exit 0 },
) or die "Invalid options. Use --help for usage.\n";

sub usage {
    print <<'END';
Usage: tool-b [OPTIONS] <input>

Options:
  -v, --verbose   Enable verbose output
  -h, --help      Show this help

END
}

my $input = shift @ARGV or do { usage(); die "Error: <input> argument required.\n" };

if ($verbose) {
    log_msg("Verbose mode enabled.");
}

my $result = uc($input);
log_msg("Result: $result");
print "$result\n";
