# Phase 3 Frontend Architecture Review: Edge Cases & Security

This report documents vulnerabilities and edge cases within the `templates/assistant.html` client-side JavaScript, specifically focusing on the chat streaming interface.

## 1. State Desync: AbortController UI Lock

**Vulnerability:** 
When the user clicks the "Stop Generation" button, the `AbortController` terminates the fetch stream early, triggering an `AbortError`. The current `try...catch` block handles `AbortError` but mistakenly skips calling `finishGeneration()`. This leaves the UI inputs disabled and the loading indicators spinning indefinitely.

**Exact JavaScript Patch:**
Move `finishGeneration()` into a `finally` block to guarantee UI reset, regardless of how the stream ends.

```javascript
// In templates/assistant.html -> submitMessage()
        } catch (err) {
            if (err.name === 'AbortError') {
                console.log("Generation aborted by user.");
            } else {
                console.error("Error during streaming:", err);
                document.getElementById('activeResponseText').innerText = "An error occurred during generation.";
            }
        } finally {
            // PATCH: Always reset UI state
            finishGeneration();
            if (!skipReload) {
                window.location.reload();
            }
        }
```
*(Note: Remove `finishGeneration()` and the reload logic from the `try` block end and `else` block to prevent double-firing).*

## 2. DOM Manipulation Risks: XSS Vulnerability

**Vulnerability:**
The `parseAndFormatMessage()` function converts the LLM's markdown to HTML using `marked.parse()` and returns it. This raw output is then injected directly into the DOM using `activeTextEl.innerHTML`. Because standard markdown parsers allow raw HTML passthrough, an LLM hallucination containing `<script>` tags or `<img src="x" onerror="alert(1)">` will execute immediately in the victim's browser.

**Exact JavaScript Patch:**
Sanitize the HTML using a library like DOMPurify before returning it.

```javascript
// In templates/assistant.html
    function parseAndFormatMessage(text) {
        let cleanText = text;
        cleanText = cleanText.replace(/\[ITEMS_JSON_START\][\s\S]*?\[ITEMS_JSON_END\]/g, "");
        let html = marked.parse(cleanText);
        // Replace [1], [2] with clickable marker links/badges
        html = html.replace(/\[([0-9]+)\]/g, '<span class="citation-marker" onclick="showCitation(this, $1)">[$1]</span>');
        
        // PATCH: Sanitize output to prevent XSS
        if (typeof DOMPurify !== 'undefined') {
            html = DOMPurify.sanitize(html);
        }
        return html;
    }
```
*(Ensure `<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.0.6/purify.min.js"></script>` is included in `base.html`).*

## 3. Network Edge Cases: SSE Drops and 500 Errors

**Vulnerability:**
If the server returns a 500 error on the initial fetch, `response.ok` triggers an exception. This completely wipes out the current response buffer and displays a generic error text, losing any context. Similarly, if the network drops mid-stream, `reader.read()` throws a `TypeError: NetworkError`, causing the exact same generic error handling, deleting whatever partial text was successfully generated.

**Exact JavaScript Patch:**
Preserve the successfully generated buffer when a mid-stream network drop occurs, and provide specific HTTP status feedback.

```javascript
// In templates/assistant.html -> submitMessage()
            const response = await fetch('/assistant/chat_stream?tab_id=' + tabId, { ... });

            if (!response.ok) {
                // PATCH: Handle initial HTTP errors gracefully
                throw new Error(`Server returned ${response.status}: ${response.statusText}`);
            }
            
            // ... streaming loop ...

        } catch (err) {
            if (err.name === 'AbortError') {
                console.log("Generation aborted by user.");
            } else {
                console.error("Error during streaming:", err);
                const activeTextEl = document.getElementById('activeResponseText');
                
                // PATCH: Append error notice instead of wiping the DOM
                const errorNotice = `<br><br><span style="color: #ef4444;">[Network Interrupted: ${err.message}]</span>`;
                if (activeTextEl.innerHTML.trim() === '') {
                    activeTextEl.innerHTML = "An error occurred during generation." + errorNotice;
                } else {
                    activeTextEl.innerHTML += errorNotice;
                }
            }
        }
```
