# Reusable knowledge sources

The KB is a retrieval-augmented generation (RAG) pipeline: the application retrieves approved articles with local BM25 ranking and supplies the matching steps and source links to the voice model. It does not train a model on the articles. The implementation does not require external vector services or embeddings.

## Good starting sources

- **Windows Server:** [MicrosoftDocs/windowsserverdocs](https://github.com/MicrosoftDocs/windowsserverdocs) publishes documentation content under CC BY 4.0; check `LICENSE`, `LICENSE-CODE`, and `ThirdPartyNotices` because code and third-party assets can have different terms.
- **Windows app and developer support:** [MicrosoftDocs/windows-dev-docs](https://github.com/MicrosoftDocs/windows-dev-docs) states that documentation and other repository content are CC BY 4.0 and code is MIT licensed.
- **Microsoft Learn content:** use only repositories that publish an applicable content license. Do not assume every page, screenshot, trademark, sample, or third-party asset in a repository has the same license.

CC BY 4.0 allows commercial sharing and adaptation when the reuse gives appropriate credit, links the license, and indicates changes. [Creative Commons license summary](https://creativecommons.org/licenses/by/4.0/). For CC BY-SA content, review the share-alike obligations before incorporating adaptations into a proprietary MSP product. Do not import Stack Exchange content for commercial use without reviewing its current API/data access terms and attribution requirements.

## Import and review

1. Pick only documentation relevant to the supported client stack. Prefer official vendor or government guidance over community answers.
2. Check the exact file's repository license and third-party notices. Record the content title, publisher/author, upstream URL, license, and what was changed.
3. In **Knowledge base → Import sourced content**, paste a concise, relevant passage or an internally rewritten runbook, enter the source URL and attribution, and import it as a draft.
4. Review every step for safety, product version, tenant-specific differences, and escalation conditions. Mark client-specific content with the exact Zoho Desk account.
5. Select **Approve and enable** only after the source rights and technical accuracy have been reviewed. The agent only retrieves enabled articles and includes their source URLs in its grounding context.

Keep the knowledge library limited to material the MSP is authorized to use. Retain attribution records, update articles when upstream guidance changes, and disable stale or unsafe instructions. A source license does not guarantee that advice is safe for a particular customer's environment.
