// Iterative Approach
while (n > 0) {
    if (n % 3 == 0) {
        n /= 3;
    } else {
        break;
    }
}
return n == 1;

// Mathematical Approach
return n > 0 && Math.pow(3, 19) % n == 0;