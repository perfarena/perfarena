function truncateSentence(s, k) {
    let arrOfString = s.split(" ");
    arrOfString.splice(k, arrOfString.length - k);
    return arrOfString.join(" ");
}