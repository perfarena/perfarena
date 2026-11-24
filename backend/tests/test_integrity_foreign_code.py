"""foreign_code: the scan flags code that starts another process, and does not
flag the usual false positives found in the 2026-11-11 language tests."""
from app.integrity import foreign_code


def test_flags_real_process_calls():
    assert foreign_code('def f\n  `ls -l`\nend', "ruby")
    assert foreign_code('def f\n  system("ls")\nend', "ruby")
    assert foreign_code('(define (f) (process "ls"))', "racket")
    assert foreign_code('(define (f) (system "ls"))', "racket")
    assert foreign_code('f() -> open_port({spawn, "ls"}, []).', "erlang")
    assert foreign_code('f() -> os:cmd("ls").', "erlang")


def test_no_false_positives():
    # a helper named `process`
    assert not foreign_code('(define (process xs) xs)\n(define (f xs) (process xs))', "racket")
    # backticks inside a string literal
    assert not foreign_code('def f\n  "use `a` and `b`"\nend', "ruby")
    assert not foreign_code("<?php function f() { return 'x `a` `b`'; }", "php")
    # the CLBG Erlang fasta reference writes stdout through a file-descriptor port
    assert not foreign_code('main(_) -> P = open_port({fd, 0, 1}, [out, binary]), P.', "erlang")


def test_ruby_and_racket_text_that_never_runs():
    # Ruby: a variable named spawn/system/exec, =begin blocks, heredocs,
    # multi-line strings, comments right after code, the char literal ?`
    assert not foreign_code("def f(a)\n  spawn = a.size\n  system = 1\n  spawn + system\nend", "ruby")
    assert not foreign_code("=begin\n obj = `x` and `y`\n=end\ndef f; end", "ruby")
    assert not foreign_code("def f\n  s = <<~EOS\n    run `ls` now `x`\n  EOS\nend", "ruby")
    assert not foreign_code('def f\n  "a `b`\n c `d`"\nend', "ruby")
    assert not foreign_code("def f\n  x = 1# `a` `b`\nend", "ruby")
    assert not foreign_code("def f\n  c = ?`\n  d = ?`\nend", "ruby")
    assert foreign_code("def f\n  exec 'ls'\nend", "ruby")
    # Racket: #| |# and #; comments, strings, ; with no space, own helper `process`
    assert not foreign_code('#| (system "ls") |#\n(define (f) 1)', "racket")
    assert not foreign_code('(define (f) "(system \\"ls\\")")', "racket")
    assert not foreign_code('(define (f) 1);(system "ls")', "racket")
    assert not foreign_code('(define (f) #;(system "ls") 1)', "racket")
    assert not foreign_code('(define (process s) s)\n(define (f) (process "abc"))', "racket")
    assert not foreign_code('(define (f) "ffi/unsafe")', "racket")
    assert foreign_code('(require ffi/unsafe)', "racket")
    assert foreign_code('(define (f) (system "ls"))', "racket")


def test_own_names_are_not_library_calls():
    assert not foreign_code("def execute(&block)\n  block.call\nend", "ruby")
    assert not foreign_code("(define (f ws) (let process ([w ws]) (if (null? w) 0 (process (cdr w)))))", "racket")
