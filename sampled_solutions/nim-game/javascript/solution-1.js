function canWinGame(n) {
    return n % 4 !== 0;
}

// Test the function with different values
console.log(canWinGame(4)); // false (Losing scenario)
console.log(canWinGame(8)); // false (Losing scenario)
console.log(canWinGame(12)); // false (Losing scenario)
console.log(canWinGame(15)); // true (Winning scenario)
console.log(canWinGame(16)); // false (Losing scenario)