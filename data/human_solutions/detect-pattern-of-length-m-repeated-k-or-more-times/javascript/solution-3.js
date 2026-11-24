function minDeciBinaryNumbers(n) {
    const nums = n.split('').map(Number);
    return Math.max(...nums);
}