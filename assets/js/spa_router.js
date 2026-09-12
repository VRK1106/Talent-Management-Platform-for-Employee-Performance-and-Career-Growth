/**
 * SPA Router for Talent Sphere Elevate
 * Intercepts internal navigation to fetch only the main content block,
 * providing a seamless, zero-glitch single-page experience.
 */

document.addEventListener("DOMContentLoaded", () => {
    // Determine the container we will swap content into
    const contentArea = document.getElementById("spa-content-area");
    if (!contentArea) return;

    // Attach click listener to the whole document to catch all link clicks
    document.body.addEventListener("click", async (e) => {
        // Find closest anchor tag
        const link = e.target.closest("a");
        if (!link) return;

        const href = link.getAttribute("href");
        
        // Ignore links that aren't for internal SPA navigation
        if (!href || href.startsWith("javascript:") || href.startsWith("#") || link.target === "_blank" || link.hasAttribute("download")) {
            return;
        }

        // Must be same origin
        if (link.origin !== window.location.origin) {
            return;
        }
        
        // Ignore logout and download routes as they should do a hard load
        if (href.includes("/logout") || href.includes("/download")) {
            return;
        }

        e.preventDefault();

        // If clicking the current URL, ignore
        if (link.href === window.location.href) {
            return;
        }

        // Navigate to the new page via SPA
        await navigateTo(link.href);
    });

    // Handle browser back/forward buttons
    window.addEventListener("popstate", async (e) => {
        await navigateTo(window.location.href, false);
    });

    /**
     * Fetches the URL and injects it into the content area.
     */
    async function navigateTo(url, pushHistory = true) {
        try {
            // Add a visual loading state to the container
            contentArea.style.opacity = '0.5';
            
            const response = await fetch(url, {
                headers: {
                    'X-SPA-Request': 'true'
                }
            });

            if (!response.ok) {
                // If the server errors, fallback to standard navigation
                window.location.href = url;
                return;
            }

            const html = await response.text();

            // If the server unexpectedly returned a full page (e.g. redirected to login),
            // we should do a full hard reload.
            if (html.includes("<!DOCTYPE html>") || html.includes("<html")) {
                window.location.href = url;
                return;
            }

            // Extract title if present and update document.title
            const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
            if (titleMatch && titleMatch[1]) {
                document.title = titleMatch[1].trim();
            }

            // Update content area
            contentArea.innerHTML = html;
            
            // Execute any scripts that came in the new HTML
            await executeScripts(contentArea);

            if (pushHistory) {
                window.history.pushState({ path: url }, "", url);
            }

            // Update active states in the sidebar
            updateActiveLinks(url);

        } catch (error) {
            console.error("SPA Navigation Error:", error);
            // Fallback to hard reload on network failure
            window.location.href = url;
        } finally {
            contentArea.style.opacity = '1';
        }
    }

    /**
     * Scripts inserted via innerHTML don't execute automatically.
     * We must manually re-create and append them sequentially to respect dependencies.
     */
    async function executeScripts(container) {
        const scripts = Array.from(container.querySelectorAll("script"));
        for (const oldScript of scripts) {
            await new Promise((resolve) => {
                const newScript = document.createElement("script");
                Array.from(oldScript.attributes).forEach(attr => newScript.setAttribute(attr.name, attr.value));
                newScript.appendChild(document.createTextNode(oldScript.innerHTML));
                
                if (newScript.src) {
                    newScript.onload = resolve;
                    newScript.onerror = resolve; // Continue on error
                    oldScript.parentNode.replaceChild(newScript, oldScript);
                } else {
                    oldScript.parentNode.replaceChild(newScript, oldScript);
                    resolve();
                }
            });
        }
        
        // Trigger DOMContentLoaded to initialize newly added components
        document.dispatchEvent(new Event("DOMContentLoaded"));
    }

    /**
     * Highlights the correct sidebar link.
     */
    function updateActiveLinks(url) {
        const { pathname } = new URL(url);
        
        document.querySelectorAll(".sb-nav-link").forEach(link => {
            link.classList.remove("active");
            const href = link.getAttribute("href");
            
            if (pathname === "/" && href === "/") {
                link.classList.add("active");
            } else if (href !== "/" && pathname.startsWith(href)) {
                link.classList.add("active");
            }
        });
    }

    /**
     * Exposed function for silent auto-reloads preserving scroll
     */
    window.tsSilentReload = async function() {
        const url = window.location.href;
        try {
            const response = await fetch(url, { headers: { 'X-SPA-Request': 'true' } });
            if (!response.ok) return;
            const html = await response.text();
            if (html.includes("<!DOCTYPE html>") || html.includes("<html")) return;
            
            // Save scroll position
            const scrollTop = window.scrollY || document.documentElement.scrollTop;
            
            // Extract title if present and update document.title
            const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
            if (titleMatch && titleMatch[1]) {
                document.title = titleMatch[1].trim();
            }

            contentArea.innerHTML = html;
            await executeScripts(contentArea);
            
            // Restore scroll position
            window.scrollTo(0, scrollTop);
        } catch (e) {
            console.error("Silent reload error", e);
        }
    };
});
