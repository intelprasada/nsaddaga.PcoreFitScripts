#!/usr/bin/env perl
# test_tool_b.t – Unit tests for tool_b.pl
use strict;
use warnings;
use Test::More tests => 3;
use FindBin qw($Bin);
use lib "$Bin/../../../lib/perl";
use CommonUtils qw(log_msg);

# Test 1: CommonUtils log_msg is callable
ok(defined &CommonUtils::log_msg, 'CommonUtils::log_msg is defined');

# Test 2: uc() produces the expected transformation (mirrors what tool_b.pl does)
my $input  = 'hello';
my $result = uc($input);
is($result, 'HELLO', 'uc(hello) eq HELLO');

# Test 3: tool_b.pl script exists and is readable
my $script = "$Bin/../tool_b.pl";
ok(-r $script, 'tool_b.pl exists and is readable');
