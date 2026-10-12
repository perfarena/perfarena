function truncateSentence(s, k) {
    let words = s.split(" ");
    let truncatedWords = words.slice(0, k);
    return truncatedWords.join(" ");
}