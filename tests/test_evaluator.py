import pytest

from pz80.evaluator import ExpressionEvaluator
from pz80.z80 import Z80


@pytest.fixture
def cpu():
    """Provides a Z80 cpu instance."""
    return Z80()


def evaluate_expression(tokens, cpu, label_map=None, defined_labels_pass1=None, line_num=1):
    """Helper function to run the evaluator."""
    evaluator = ExpressionEvaluator(tokens, label_map, line_num, cpu, defined_labels_pass1)
    return evaluator.evaluate()


# Basic tests
def test_simple_integer(cpu):
    assert evaluate_expression(["123"], cpu) == 123


def test_hex_integer(cpu):
    assert evaluate_expression(["0xFF"], cpu) == 255


def test_simple_addition(cpu):
    assert evaluate_expression(["10", "+", "5"], cpu) == 15


def test_simple_subtraction(cpu):
    assert evaluate_expression(["10", "-", "3"], cpu) == 7


def test_simple_multiplication(cpu):
    assert evaluate_expression(["10", "*", "3"], cpu) == 30


def test_simple_division(cpu):
    assert evaluate_expression(["10", "/", "2"], cpu) == 5


# Precedence and parentheses
def test_operator_precedence(cpu):
    assert evaluate_expression(["10", "+", "2", "*", "3"], cpu) == 16


def test_operator_precedence_2(cpu):
    assert evaluate_expression(["10", "*", "2", "+", "3"], cpu) == 23


def test_parentheses_override_precedence(cpu):
    assert evaluate_expression(["(", "10", "+", "2", ")", "*", "3"], cpu) == 36


def test_nested_parentheses(cpu):
    assert evaluate_expression(["(", "(", "1", "+", "1", ")", "*", "2", ")", "*", "3"], cpu) == 12


# Unary operators
def test_unary_minus(cpu):
    assert evaluate_expression(["-", "10"], cpu) == -10


def test_unary_plus(cpu):
    assert evaluate_expression(["+", "10"], cpu) == 10


def test_unary_in_expression(cpu):
    assert evaluate_expression(["5", "*", "-", "2"], cpu) == -10


# Character literals
def test_single_char_literal(cpu):
    assert evaluate_expression(["'A'"], cpu) == 65


def test_double_char_literal(cpu):
    assert evaluate_expression(["'AB'"], cpu) == 0x4142


def test_double_quote_char_literal(cpu):
    assert evaluate_expression(['"C"'], cpu) == 67


# Label evaluation
def test_pass1_evaluation_with_defined_label(cpu):
    # Pass 1では、ラベルは0として解決されるべき
    defined_labels = {"MY_LABEL"}
    assert evaluate_expression(["MY_LABEL"], cpu, defined_labels_pass1=defined_labels) == 0


def test_pass1_evaluation_with_expression(cpu):
    defined_labels = {"MY_LABEL"}
    assert evaluate_expression(["MY_LABEL", "+", "5"], cpu, defined_labels_pass1=defined_labels) == 5


def test_pass2_evaluation_with_defined_label(cpu):
    label_map = {"MY_LABEL": 0x1234}
    assert evaluate_expression(["MY_LABEL"], cpu, label_map=label_map) == 0x1234


def test_pass2_evaluation_with_expression(cpu):
    label_map = {"L1": 0x100, "L2": 0x200}
    assert evaluate_expression(["L1", "+", "L2", "-", "1"], cpu, label_map=label_map) == 0x100 + 0x200 - 1


# Error handling
def test_error_division_by_zero(cpu):
    with pytest.raises(ValueError, match="Division by zero"):
        evaluate_expression(["10", "/", "0"], cpu)


def test_error_mismatched_parentheses(cpu):
    with pytest.raises(ValueError, match="Mismatched parentheses"):
        evaluate_expression(["(", "10", "+", "5"], cpu)


def test_error_pass1_undefined_label(cpu):
    defined_labels = {"SOME_LABEL"}
    with pytest.raises(ValueError, match="Undefined symbol 'UNDEFINED'"):
        evaluate_expression(["UNDEFINED"], cpu, defined_labels_pass1=defined_labels)


def test_error_pass2_undefined_label(cpu):
    label_map = {"SOME_LABEL": 1}
    with pytest.raises(ValueError, match="Undefined label or invalid term 'UNDEFINED'"):
        evaluate_expression(["UNDEFINED"], cpu, label_map=label_map)


def test_error_reserved_word(cpu):
    with pytest.raises(ValueError, match="Reserved word 'ld'"):
        evaluate_expression(["ld"], cpu)


def test_error_long_char_literal(cpu):
    with pytest.raises(ValueError, match="String literal in expression must be 1 or 2 characters"):
        evaluate_expression(["'ABC'"], cpu)
