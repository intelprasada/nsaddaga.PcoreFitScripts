package CommonUtils;
# CommonUtils.pm – Shared Perl utilities for core-tools
use strict;
use warnings;
use Exporter 'import';

our @EXPORT_OK = qw(log_msg);

sub log_msg {
    my ($msg) = @_;
    print "$msg\n";
}

1;
