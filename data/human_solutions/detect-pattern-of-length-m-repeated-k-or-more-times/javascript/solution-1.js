function findMinDecibinaryNumbers(n) {
    let maxDigit = 0;
    
    for (let i = 0; i < n.length; i++) {
        maxDigit = Math.max(maxDigit, Number(n[i]));
    }
    
    return maxDigit;
}

// Test the function
console.log(findMinDecibinaryNumbers("327")); // Output: 7