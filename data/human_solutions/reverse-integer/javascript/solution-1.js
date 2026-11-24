const reverseInteger = (x) => {
    if (x < 0) {
        return -1 * reverseInteger(-x);
    }
    
    const solution = parseInt(x.toString().split('').reverse().join(''));
    
    return solution > 2**31 - 1 ? 0 : solution;
};

// Test the function
console.log(reverseInteger(123)); // Output: 321
console.log(reverseInteger(-123)); // Output: -321
console.log(reverseInteger(1200)); // Output: 21