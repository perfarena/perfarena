function minDeciBinaryNumbers(n) {
    let largest = 0;
    for (let i = 0; i < n.length; i++) {
        largest = Math.max(largest, parseInt(n.charAt(i), 10));
    }
    return largest;
}