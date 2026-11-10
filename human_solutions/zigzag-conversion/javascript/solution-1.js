function convertToZigZag(s, numRows) {
    if (numRows === 1 || s.length < numRows) {
        return s;
    }

    const rows = Array.from({ length: numRows }, () => []);
    let count = 0;
    let reverse = false;

    for (let i = 0; i < s.length; i++) {
        rows[count].push(s[i]);

        if (reverse) {
            count--;
        } else {
            count++;
        }

        if (count === numRows - 1 || count === 0) {
            reverse = !reverse;
        }
    }

    return rows.reduce((prev, curr) => prev + curr.join(''), '');
}

// Test the function with an example
const inputString = 'PAYPALISHIRING';
const numRows = 3;
const zigzagPattern = convertToZigZag(inputString, numRows);
console.log(zigzagPattern);