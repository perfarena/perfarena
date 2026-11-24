function reverseInteger(X) {
    let reverse = 0;
    let negative = X < 0;

    if (negative) {
        X *= -1;
    }

    while (X > 0) {
        reverse = reverse * 10 + (X % 10);
        X = Math.floor(X / 10);
    }

    if (reverse > 2**31 - 1) {
        return 0;
    }

    return negative ? reverse * -1 : reverse;
}