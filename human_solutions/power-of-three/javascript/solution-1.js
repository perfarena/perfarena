function checkResult(input) {
    let result = [];
    while (input >= 3) {
        input = Math.floor(input / 3);
        result.push(input % 3);
    }
    return result.every(num => num === 0);
}